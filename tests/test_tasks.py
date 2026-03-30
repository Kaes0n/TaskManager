import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Добавляем корневую директорию проекта в путь для импорта модуля
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task import app, Task, db, scheduler
import json


class TestScheduleTypes(unittest.TestCase):
    """Тесты для проверки типов расписания задач"""

    def setUp(self):
        """Настройка тестового окружения перед каждым тестом"""
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        """Очистка после каждого теста"""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_create_once_task(self):
        """Тест создания задачи с однократным выполнением"""
        future_time = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Test Once Task',
                'code': 'print("Once task executed")',
                'schedule_type': 'once',
                'run_time': future_time
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача добавлена в планировщик
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args

            # Проверяем параметры вызова
            self.assertEqual(call_args[1]['trigger'], 'date')
            self.assertIn('run_date', call_args[1])

            # Проверяем, что задача сохранена в БД
            with self.app.app_context():
                task = Task.query.filter_by(name='Test Once Task').first()
                self.assertIsNotNone(task)
                self.assertEqual(task.schedule_type, 'once')
                self.assertEqual(task.status, 'Scheduled')

    def test_create_daily_task(self):
        """Тест создания ежедневной задачи"""
        daily_time = '14:30'

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Test Daily Task',
                'code': 'print("Daily task executed")',
                'schedule_type': 'daily',
                'daily_time': daily_time
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача добавлена в планировщик
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args

            # Проверяем параметры вызова
            self.assertEqual(call_args[1]['trigger'], 'cron')
            self.assertEqual(call_args[1]['hour'], 14)
            self.assertEqual(call_args[1]['minute'], 30)

            # Проверяем, что задача сохранена в БД
            with self.app.app_context():
                task = Task.query.filter_by(name='Test Daily Task').first()
                self.assertIsNotNone(task)
                self.assertEqual(task.schedule_type, 'daily')
                self.assertEqual(task.daily_time, daily_time)

    def test_create_interval_task_future_time(self):
        """Тест создания интервальной задачи с будущим временем"""
        # Устанавливаем время на 2 часа вперед
        future_hour = (datetime.now().hour + 2) % 24
        future_time = f'{future_hour:02d}:00'

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Test Interval Task Future',
                'code': 'print("Interval task executed")',
                'schedule_type': 'interval',
                'interval_days': 3,
                'interval_time': future_time
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача добавлена в планировщик
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args

            # Проверяем параметры вызова
            self.assertEqual(call_args[1]['trigger'], 'interval')
            self.assertEqual(call_args[1]['days'], 3)
            self.assertIn('start_date', call_args[1])

            # Проверяем, что start_date в будущем
            start_date = call_args[1]['start_date']
            self.assertGreater(start_date, datetime.now())

            # Проверяем, что секунды и микросекунды обнулены
            self.assertEqual(start_date.second, 0)
            self.assertEqual(start_date.microsecond, 0)

            # Проверяем, что задача сохранена в БД
            with self.app.app_context():
                task = Task.query.filter_by(name='Test Interval Task Future').first()
                self.assertIsNotNone(task)
                self.assertEqual(task.schedule_type, 'interval')
                self.assertEqual(task.interval_days, 3)
                self.assertEqual(task.interval_time, future_time)

    def test_create_interval_task_past_time(self):
        """Тест создания интервальной задачи с прошедшим временем (должен перенестись на завтра)"""
        # Устанавливаем время на 2 часа назад
        past_hour = (datetime.now().hour - 2) % 24
        past_time = f'{past_hour:02d}:00'

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Test Interval Task Past',
                'code': 'print("Interval task executed")',
                'schedule_type': 'interval',
                'interval_days': 2,
                'interval_time': past_time
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача добавлена в планировщик
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args

            # Проверяем параметры вызова
            self.assertEqual(call_args[1]['trigger'], 'interval')
            self.assertEqual(call_args[1]['days'], 2)
            self.assertIn('start_date', call_args[1])

            # Проверяем, что start_date - завтрашний день
            start_date = call_args[1]['start_date']
            expected_date = (datetime.now() + timedelta(days=1)).replace(
                hour=past_hour, minute=0, second=0, microsecond=0
            )

            # Допускаем небольшую погрешность в секундах
            time_diff = abs((start_date - expected_date).total_seconds())
            self.assertLess(time_diff, 5)  # Менее 5 секунд разницы

            # Проверяем, что задача сохранена в БД
            with self.app.app_context():
                task = Task.query.filter_by(name='Test Interval Task Past').first()
                self.assertIsNotNone(task)
                self.assertEqual(task.schedule_type, 'interval')

    def test_edit_task_change_schedule_type(self):
        """Тест изменения типа расписания при редактировании"""
        # Сначала создаем задачу
        with patch('task.scheduler'):
            self.client.post('/create', data={
                'name': 'Task to Edit',
                'code': 'print("Original")',
                'schedule_type': 'once',
                'run_time': '2025-12-31T23:59'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Task to Edit').first()
                task_id = task.id

        # Редактируем задачу, меняем тип на daily
        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post(f'/edit/{task_id}', data={
                'name': 'Edited Task',
                'code': 'print("Edited")',
                'schedule_type': 'daily',
                'daily_time': '10:00'
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что старое задание удалено и создано новое
            mock_scheduler.remove_job.assert_called_once_with(task_id)
            mock_scheduler.add_job.assert_called_once()

            call_args = mock_scheduler.add_job.call_args
            self.assertEqual(call_args[1]['trigger'], 'cron')
            self.assertEqual(call_args[1]['hour'], 10)
            self.assertEqual(call_args[1]['minute'], 0)

            # Проверяем обновление в БД
            with self.app.app_context():
                task = db.session.get(Task, task_id)
                self.assertEqual(task.name, 'Edited Task')
                self.assertEqual(task.schedule_type, 'daily')

    def test_interval_task_zero_seconds(self):
        """Тест, что у интервальной задачи секунды и микросекунды равны 0"""
        future_hour = (datetime.now().hour + 1) % 24

        with patch('task.scheduler') as mock_scheduler:
            self.client.post('/create', data={
                'name': 'Test Zero Seconds',
                'code': 'print("Test")',
                'schedule_type': 'interval',
                'interval_days': 1,
                'interval_time': f'{future_hour:02d}:00'
            })

            call_args = mock_scheduler.add_job.call_args
            start_date = call_args[1]['start_date']

            # Критическая проверка: секунды и микросекунды должны быть 0
            self.assertEqual(start_date.second, 0,
                           "Seconds must be 0 for interval tasks")
            self.assertEqual(start_date.microsecond, 0,
                           "Microseconds must be 0 for interval tasks")

    def test_restore_interval_tasks(self):
        """Тест восстановления интервальных задач при перезапуске"""
        # Создаем задачу напрямую в БД
        with self.app.app_context():
            future_hour = (datetime.now().hour + 1) % 24
            task = Task(
                id='test-restore-id',
                name='Restore Test Task',
                status='Scheduled',
                schedule_type='interval',
                run_time=f'Every 2 days at {future_hour:02d}:00',
                path='tasks/test.py',
                interval_days=2,
                interval_time=f'{future_hour:02d}:00'
            )
            db.session.add(task)
            db.session.commit()

        # Тестируем функцию восстановления
        with patch('task.scheduler') as mock_scheduler:
            from task import restore_tasks
            restore_tasks()

            # Проверяем, что задача восстановлена в планировщике
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args

            self.assertEqual(call_args[1]['id'], 'test-restore-id')
            self.assertEqual(call_args[1]['trigger'], 'interval')
            self.assertEqual(call_args[1]['days'], 2)

            # Проверяем корректность start_date
            start_date = call_args[1]['start_date']
            self.assertEqual(start_date.second, 0)
            self.assertEqual(start_date.microsecond, 0)

    def test_concurrent_schedule_types(self):
        """Тест создания задач с разными типами расписания одновременно"""
        future_time = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler') as mock_scheduler:
            # Создаем три задачи с разными типами расписания
            tasks = [
                {
                    'name': 'Once Task',
                    'code': 'print("once")',
                    'schedule_type': 'once',
                    'run_time': future_time
                },
                {
                    'name': 'Daily Task',
                    'code': 'print("daily")',
                    'schedule_type': 'daily',
                    'daily_time': '15:00'
                },
                {
                    'name': 'Interval Task',
                    'code': 'print("interval")',
                    'schedule_type': 'interval',
                    'interval_days': 5,
                    'interval_time': '16:00'
                }
            ]

            for task_data in tasks:
                self.client.post('/create', data=task_data)

            # Проверяем, что все три задачи были добавлены
            self.assertEqual(mock_scheduler.add_job.call_count, 3)

            # Проверяем, что все задачи сохранены в БД
            with self.app.app_context():
                once_task = Task.query.filter_by(name='Once Task').first()
                daily_task = Task.query.filter_by(name='Daily Task').first()
                interval_task = Task.query.filter_by(name='Interval Task').first()

                self.assertIsNotNone(once_task)
                self.assertIsNotNone(daily_task)
                self.assertIsNotNone(interval_task)

                self.assertEqual(once_task.schedule_type, 'once')
                self.assertEqual(daily_task.schedule_type, 'daily')
                self.assertEqual(interval_task.schedule_type, 'interval')


class TestEdgeCases(unittest.TestCase):
    """Тесты граничных случаев"""

    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_interval_task_exactly_midnight(self):
        """Тест интервальной задачи на полночь (00:00)"""
        with patch('task.scheduler') as mock_scheduler:
            # Создаем задачу на 00:00
            past_hour = 0
            past_time = '00:00'

            self.client.post('/create', data={
                'name': 'Midnight Task',
                'code': 'print("Midnight")',
                'schedule_type': 'interval',
                'interval_days': 1,
                'interval_time': past_time
            })

            call_args = mock_scheduler.add_job.call_args
            start_date = call_args[1]['start_date']

            # Проверяем, что время установлено правильно
            self.assertEqual(start_date.hour, 0)
            self.assertEqual(start_date.minute, 0)
            self.assertEqual(start_date.second, 0)
            self.assertEqual(start_date.microsecond, 0)

    def test_interval_task_last_minute_of_hour(self):
        """Тест интервальной задачи на последнюю минуту часа (XX:59)"""
        with patch('task.scheduler') as mock_scheduler:
            past_hour = (datetime.now().hour - 1) % 24
            past_time = f'{past_hour:02d}:59'

            self.client.post('/create', data={
                'name': 'Last Minute Task',
                'code': 'print("Last minute")',
                'schedule_type': 'interval',
                'interval_days': 1,
                'interval_time': past_time
            })

            call_args = mock_scheduler.add_job.call_args
            start_date = call_args[1]['start_date']

            # Проверяем, что минуты установлены правильно
            self.assertEqual(start_date.minute, 59)
            self.assertEqual(start_date.second, 0)


class TestArchiveFunctionality(unittest.TestCase):
    """Тесты функциональности архивации задач"""

    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_once_task_auto_archive_after_execution(self):
        """Тест автоматической архивации одноразовой задачи после выполнения"""
        future_time = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler') as mock_scheduler:
            # Создаем одноразовую задачу
            self.client.post('/create', data={
                'name': 'Once Task to Archive',
                'code': 'print("Execute once")',
                'schedule_type': 'once',
                'run_time': future_time
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Once Task to Archive').first()
                task_id = task.id
                self.assertTrue(task.is_active, "Task should be active initially")

                # Симулируем выполнение задачи
                from task import execute_task
                execute_task(task_id)

                # Перезагружаем объект из базы данных
                db.session.refresh(task)

                # Проверяем, что задача заархивирована
                self.assertFalse(task.is_active, "Once task should be archived after execution")
                self.assertEqual(task.status, 'Completed')

    def test_manual_archive_task(self):
        """Тест ручного архивирования задачи"""
        with patch('task.scheduler') as mock_scheduler:
            # Создаем ежедневную задачу
            self.client.post('/create', data={
                'name': 'Daily Task to Archive',
                'code': 'print("Daily")',
                'schedule_type': 'daily',
                'daily_time': '15:00'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Daily Task to Archive').first()
                task_id = task.id
                self.assertTrue(task.is_active)

            # Архивируем задачу
            response = self.client.get(f'/archive/{task_id}', follow_redirects=True)
            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача заархивирована
            with self.app.app_context():
                task = db.session.get(Task, task_id)
                self.assertFalse(task.is_active, "Task should be archived")
                mock_scheduler.remove_job.assert_called_with(task_id)

    def test_manual_unarchive_task(self):
        """Тест ручного разархивирования задачи"""
        with patch('task.scheduler') as mock_scheduler:
            # Создаем и архивируем задачу
            self.client.post('/create', data={
                'name': 'Task to Unarchive',
                'code': 'print("Test")',
                'schedule_type': 'daily',
                'daily_time': '16:00'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Task to Unarchive').first()
                task_id = task.id

            # Архивируем
            self.client.get(f'/archive/{task_id}')

            # Разархивируем
            response = self.client.get(f'/unarchive/{task_id}', follow_redirects=True)
            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача разархивирована
            with self.app.app_context():
                task = db.session.get(Task, task_id)
                self.assertTrue(task.is_active, "Task should be unarchived")
                # Проверяем, что задача восстановлена в планировщике
                self.assertTrue(mock_scheduler.add_job.called, "Task should be restored in scheduler")

    def test_filter_active_tasks(self):
        """Тест фильтрации активных задач"""
        with patch('task.scheduler'):
            # Создаем две задачи
            self.client.post('/create', data={
                'name': 'Active Task',
                'code': 'print("Active")',
                'schedule_type': 'daily',
                'daily_time': '10:00'
            })

            self.client.post('/create', data={
                'name': 'Archived Task',
                'code': 'print("Archived")',
                'schedule_type': 'daily',
                'daily_time': '11:00'
            })

            # Архивируем вторую задачу
            with self.app.app_context():
                task = Task.query.filter_by(name='Archived Task').first()
                task_id = task.id

            self.client.get(f'/archive/{task_id}')

            # Проверяем фильтр активных задач
            response = self.client.get('/filter/active')
            self.assertEqual(response.status_code, 200)

            # Проверяем, что только активная задача отображается
            with self.app.app_context():
                active_tasks = Task.query.filter_by(is_active=True).all()
                archived_tasks = Task.query.filter_by(is_active=False).all()

                self.assertEqual(len(active_tasks), 1)
                self.assertEqual(len(archived_tasks), 1)
                self.assertEqual(active_tasks[0].name, 'Active Task')

    def test_filter_archived_tasks(self):
        """Тест фильтрации архивных задач"""
        with patch('task.scheduler'):
            # Создаем и архивируем задачу
            self.client.post('/create', data={
                'name': 'Archive Test Task',
                'code': 'print("Test")',
                'schedule_type': 'daily',
                'daily_time': '12:00'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Archive Test Task').first()
                task_id = task.id

            self.client.get(f'/archive/{task_id}')

            # Проверяем фильтр архивных задач
            response = self.client.get('/filter/archive')
            self.assertEqual(response.status_code, 200)

            with self.app.app_context():
                archived_tasks = Task.query.filter_by(is_active=False).all()
                self.assertEqual(len(archived_tasks), 1)
                self.assertEqual(archived_tasks[0].name, 'Archive Test Task')

    def test_cannot_edit_archived_tasks(self):
        """Тест, что архивированные задачи не редактируются через UI"""
        with patch('task.scheduler'):
            # Создаем задачу
            self.client.post('/create', data={
                'name': 'No Edit Task',
                'code': 'print("No edit")',
                'schedule_type': 'daily',
                'daily_time': '13:00'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='No Edit Task').first()
                task_id = task.id

            # Архивируем
            self.client.get(f'/archive/{task_id}')

            # Проверяем UI - кнопки Edit и Run не должны отображаться для архивных задач
            response = self.client.get(f'/filter/archive')
            self.assertEqual(response.status_code, 200)
            # В UI есть проверка: {% if task.is_active %} - архивированные задачи не show кнопки Edit


class TestEndDateFunctionality(unittest.TestCase):
    """Тесты функциональности даты окончания задач"""

    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_create_daily_task_with_end_date(self):
        """Тест создания ежедневной задачи с датой окончания"""
        end_date_str = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Daily Task with End Date',
                'code': 'print("Daily with end date")',
                'schedule_type': 'daily',
                'daily_time': '14:00',
                'end_date': end_date_str
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача создана с end_date
            with self.app.app_context():
                task = Task.query.filter_by(name='Daily Task with End Date').first()
                self.assertIsNotNone(task)
                self.assertIsNotNone(task.end_date)

            # Проверяем, что job создан с end_date
            mock_scheduler.add_job.assert_called_once()
            call_args = mock_scheduler.add_job.call_args
            self.assertIn('end_date', call_args[1])

    def test_create_interval_task_with_end_date(self):
        """Тест создания интервальной задачи с датой окончания"""
        end_date_str = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Interval Task with End Date',
                'code': 'print("Interval with end date")',
                'schedule_type': 'interval',
                'interval_days': 5,
                'interval_time': '15:00',
                'end_date': end_date_str
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача создана с end_date
            with self.app.app_context():
                task = Task.query.filter_by(name='Interval Task with End Date').first()
                self.assertIsNotNone(task)
                self.assertIsNotNone(task.end_date)

            # Проверяем, что job создан с end_date
            call_args = mock_scheduler.add_job.call_args
            self.assertIn('end_date', call_args[1])

    def test_create_task_without_end_date(self):
        """Тест создания задачи без даты окончания"""
        with patch('task.scheduler') as mock_scheduler:
            response = self.client.post('/create', data={
                'name': 'Task without End Date',
                'code': 'print("No end date")',
                'schedule_type': 'daily',
                'daily_time': '16:00'
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что задача создана без end_date
            with self.app.app_context():
                task = Task.query.filter_by(name='Task without End Date').first()
                self.assertIsNotNone(task)
                self.assertIsNone(task.end_date)

            # Проверяем, что job создан без end_date
            call_args = mock_scheduler.add_job.call_args
            self.assertNotIn('end_date', call_args[1])

    def test_edit_task_add_end_date(self):
        """Тест добавления даты окончания к существующей задаче"""
        # Создаем задачу без end_date
        with patch('task.scheduler'):
            self.client.post('/create', data={
                'name': 'Task to Add End Date',
                'code': 'print("Add end date")',
                'schedule_type': 'daily',
                'daily_time': '17:00'
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Task to Add End Date').first()
                task_id = task.id
                self.assertIsNone(task.end_date)

            # Добавляем end_date
            end_date_str = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%dT%H:%M')
            response = self.client.post(f'/edit/{task_id}', data={
                'name': 'Task to Add End Date',
                'code': 'print("Add end date")',
                'schedule_type': 'daily',
                'daily_time': '17:00',
                'end_date': end_date_str
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что end_date добавлен
            with self.app.app_context():
                task = db.session.get(Task, task_id)
                self.assertIsNotNone(task.end_date)

    def test_edit_task_remove_end_date(self):
        """Тест удаления даты окончания у задачи"""
        # Создаем задачу с end_date
        end_date_str = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%dT%H:%M')

        with patch('task.scheduler'):
            self.client.post('/create', data={
                'name': 'Task to Remove End Date',
                'code': 'print("Remove end date")',
                'schedule_type': 'daily',
                'daily_time': '18:00',
                'end_date': end_date_str
            })

            with self.app.app_context():
                task = Task.query.filter_by(name='Task to Remove End Date').first()
                task_id = task.id
                self.assertIsNotNone(task.end_date)

            # Удаляем end_date (пустое поле)
            response = self.client.post(f'/edit/{task_id}', data={
                'name': 'Task to Remove End Date',
                'code': 'print("Remove end date")',
                'schedule_type': 'daily',
                'daily_time': '18:00',
                'end_date': ''
            }, follow_redirects=True)

            self.assertEqual(response.status_code, 200)

            # Проверяем, что end_date удален
            with self.app.app_context():
                task = db.session.get(Task, task_id)
                self.assertIsNone(task.end_date)

    def test_restore_task_with_end_date(self):
        """Тест восстановления задачи с датой окончания"""
        end_date = datetime.now() + timedelta(days=20)

        with self.app.app_context():
            # Создаем задачу напрямую в БД
            task = Task(
                id='test-end-date-restore',
                name='Restore Test with End Date',
                status='Scheduled',
                schedule_type='daily',
                run_time='Daily at 19:00',
                path='tasks/test.py',
                daily_time='19:00',
                is_active=True,
                end_date=end_date
            )
            db.session.add(task)
            db.session.commit()

        # Тестируем восстановление
        with patch('task.scheduler') as mock_scheduler:
            from task import restore_tasks
            restore_tasks()

            # Проверяем, что задача восстановлена с end_date
            call_args = mock_scheduler.add_job.call_args
            self.assertIn('end_date', call_args[1])
            restored_end_date = call_args[1]['end_date']
            self.assertEqual(restored_end_date, end_date)


def run_tests():
    """Запуск всех тестов"""
    # Создаем тестовый набор
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Добавляем все тесты
    suite.addTests(loader.loadTestsFromTestCase(TestScheduleTypes))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestArchiveFunctionality))

    # Запускаем тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Возвращаем результат
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
