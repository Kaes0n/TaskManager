from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_apscheduler import APScheduler
from datetime import datetime, timedelta
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.base import JobLookupError
from flask_sqlalchemy import SQLAlchemy
import os
import uuid
import json
import logging

# Версия сервиса
VERSION = '1.2.3'  # Major.Minor.Patch
# История версий:
# 1.2.3 - Валидация полей при редактировании
#   - Добавлена проверка пустых полей времени при смене типа задачи
#   - Добавлены понятные сообщения об ошибках при валидации
#   - Исправлен ValueError при изменении типа архивной задачи
# 1.2.2 - Ручной запуск архивных задач
#   - Добавлена кнопка "Run Now" для архивных задач
#   - Добавлена кнопка "Edit" для архивных задач
#   - При ручном запуске задача остаётся в своём текущем состоянии (активная/архивная)
#   - При автоматическом выполнении Once задачи архивируются как раньше
#   - Исправлена ошибка JobLookupError в планировщике
# 1.2.1 - Улучшена разархивация задач
#   - Once задачи с прошедшим временем теперь перенаправляются на редактирование
#   - Добавлены flash-сообщения для уведомлений
#   - Пользователь может указать новое время для устаревших задач
# 1.2.0 - Добавлена дата окончания задач
#   - Опциональная настройка даты окончания для Daily и Interval типов
#   - Once задачи не имеют end_date (выполняются один раз и архивируются)
#   - Задачи автоматически перестают выполняться после достижения end_date
#   - Добавлено отображение end_date в таблице задач
#   - Обновлены формы создания и редактирования задач
#   - Исправлен порядок определения функций (execute_task до restore_tasks)
#   - 23 unit-теста (включая тесты для end_date)
# 1.1.0 - Система архивации и оптимизация БД
#   - Добавлено разделение на активные и архивированные задачи
#   - Автоматическая архивация одноразовых задач после выполнения
#   - Фильтры: Все/Активные/Архив
#   - Ручное архивирование/разархивирование задач
#   - Объединение баз данных (taskmanager.db вместо taskmanager.db + jobs.sqlite)
#   - 16 unit-тестов для проверки функциональности
# 1.0.0 - Первая стабильная версия
#   - Создание, редактирование, удаление задач
#   - Поддержка разных типов расписания (однократное, ежедневное, интервальное)
#   - История выполнения задач
#   - Хранение данных в SQLite
#   - Автоматическое восстановление задач при перезапуске

app = Flask(__name__)
# Конфигурация базы данных
# Используем абсолютный путь, чтобы Flask не создавал директорию instance/
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taskmanager.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
db = SQLAlchemy(app)

# Модель для хранения истории выполнения задач
class TaskHistory(db.Model):
    __table_args__ = (
        db.Index('idx_task_id_start_time', 'task_id', 'start_time'),
    )
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(50), nullable=False)
    task_name = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False)
    output = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S') if self.end_time else None,
            'task_name': self.task_name,
            'status': self.status,
            'output': self.output,
            'error': self.error
        }

# После определения модели TaskHistory добавим новую модель
class Task(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    schedule_type = db.Column(db.String(20), nullable=False)
    run_time = db.Column(db.String(100), nullable=False)
    path = db.Column(db.String(200), nullable=False)
    # Дополнительные поля для разных типов расписания
    daily_time = db.Column(db.String(10), nullable=True)
    interval_days = db.Column(db.Integer, nullable=True)
    interval_time = db.Column(db.String(10), nullable=True)
    # Поле для архивации: True - активная, False - в архиве
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Опциональная дата окончания действия задачи
    end_date = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'schedule_type': self.schedule_type,
            'run_time': self.run_time,
            'path': self.path,
            'daily_time': self.daily_time,
            'interval_days': self.interval_days,
            'interval_time': self.interval_time,
            'is_active': self.is_active,
            'end_date': self.end_date.strftime('%Y-%m-%d %H:%M') if self.end_date else None
        }
    
def restore_tasks():
    """Восстанавливает задачи в планировщике при запуске сервера"""
    with app.app_context():
        tasks = Task.query.all()
        for task in tasks:
            try:
                if task.schedule_type == 'once':
                    # Проверяем, не прошло ли время выполнения
                    run_time = datetime.strptime(task.run_time, '%Y-%m-%dT%H:%M')
                    if run_time > datetime.now():
                        scheduler.add_job(
                            id=task.id,
                            func=execute_task,
                            args=[task.id],
                            trigger='date',
                            run_date=run_time
                        )
                elif task.schedule_type == 'daily':
                    hour, minute = map(int, task.daily_time.split(':'))
                    job_kwargs = {
                        'id': task.id,
                        'func': execute_task,
                        'args': [task.id],
                        'trigger': 'cron',
                        'hour': hour,
                        'minute': minute
                    }
                    # Добавляем end_date если указан
                    if task.end_date:
                        job_kwargs['end_date'] = task.end_date
                    scheduler.add_job(**job_kwargs)
                elif task.schedule_type == 'interval':
                    hour, minute = map(int, task.interval_time.split(':'))
                    # Вычисляем правильное время запуска
                    now = datetime.now()
                    start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # Если время уже прошло, добавляем один день
                    if start_date <= now:
                        start_date += timedelta(days=1)
                    job_kwargs = {
                        'id': task.id,
                        'func': execute_task,
                        'args': [task.id],
                        'trigger': 'interval',
                        'days': task.interval_days,
                        'start_date': start_date
                    }
                    # Добавляем end_date если указан
                    if task.end_date:
                        job_kwargs['end_date'] = task.end_date
                    scheduler.add_job(**job_kwargs)
            except Exception as e:
                logging.error(f'Error restoring task {task.id}: {str(e)}')

# Инициализация планировщика
scheduler = APScheduler()
scheduler.init_app(app)

# Инициализируем scheduler только если не в режиме тестирования
# В тестах scheduler будет мокаться, чтобы не создавать файлы БД
if not app.config.get('TESTING'):
    # Используем ту же базу данных, что и для Flask-SQLAlchemy
    scheduler.scheduler.add_jobstore(SQLAlchemyJobStore(url=f'sqlite:///{db_path}'), 'default')

    # Создаем таблицы
    with app.app_context():
        db.create_all()

    # Настраиваем параметры планировщика по умолчанию
    from apscheduler.executors.pool import ThreadPoolExecutor
    scheduler.scheduler.configure(
        job_defaults={
            'coalesce': True,  # Объединять пропущенные выполнения в одно
            'max_instances': 1,  # Не допускать параллельного выполнения одной задачи
            'misfire_grace_time': 3600  # Время ожидания для пропущенных задач (1 час)
        },
        executors={
            'default': ThreadPoolExecutor(20)
        }
    )

    # Запускаем планировщик после создания таблиц
    scheduler.start()

    # Добавляем обработчик для подавления ошибок JobLookupError в потоке планировщика
    import logging
    original_remove_job = scheduler.scheduler.remove_job

    def remove_job_safe(job_id, jobstore=None):
        try:
            return original_remove_job(job_id, jobstore)
        except JobLookupError:
            logging.debug(f'Job {job_id} not found in scheduler (already removed)')
            # Игнорируем ошибку, задача уже удалена

    scheduler.scheduler.remove_job = remove_job_safe
logging.basicConfig(filename='task_manager.log', level=logging.INFO, format='%(asctime)s - %(message)s')

TASKS_DIR = 'tasks'
os.makedirs(TASKS_DIR, exist_ok=True)

@app.route('/')
@app.route('/filter/<filter_type>')
def index(filter_type='all'):
    query = Task.query

    # Применяем фильтрацию
    if filter_type == 'active':
        query = query.filter_by(is_active=True)
    elif filter_type == 'archive':
        query = query.filter_by(is_active=False)

    tasks_list = query.all()
    return render_template('index.html', tasks=tasks_list, version=VERSION, filter_type=filter_type)

@app.route('/create', methods=['GET', 'POST'])
def create_task():
    if request.method == 'POST':
        task_id = str(uuid.uuid4())
        task_name = request.form['name']
        # Удаляем лишние пробелы и переносы строк
        code = request.form['code'].strip()
        schedule_type = request.form['schedule_type']

        # Обрабатываем end_date (опциональное поле)
        end_date = None
        end_date_str = request.form.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass  # Если формат неверный, игнорируем

        task_path = os.path.join(TASKS_DIR, f'{task_id}.py')
        # Используем newline='' при записи
        with open(task_path, 'w', newline='') as f:
            f.write(code)

        task = Task(
            id=task_id,
            name=task_name,
            status='Scheduled',
            schedule_type=schedule_type,
            path=task_path,
            end_date=end_date
        )

        if schedule_type == 'once':
            run_time = request.form['run_time']
            task.run_time = run_time
            # Для одноразовых задач end_date не имеет смысла, но можно добавить для информации
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='date',
                run_date=run_time
            )
            logging.info(f'Task {task_name} scheduled for {run_time}')

        elif schedule_type == 'daily':
            daily_time = request.form['daily_time']
            hour, minute = map(int, daily_time.split(':'))
            task.run_time = f'Daily at {daily_time}'
            task.daily_time = daily_time
            job_kwargs = {
                'id': task_id,
                'func': execute_task,
                'args': [task_id],
                'trigger': 'cron',
                'hour': hour,
                'minute': minute
            }
            # Добавляем end_date если указан
            if end_date:
                job_kwargs['end_date'] = end_date
            scheduler.add_job(**job_kwargs)
            logging.info(f'Task {task_name} scheduled daily at {daily_time}' + (f' until {end_date}' if end_date else ''))

        elif schedule_type == 'interval':
            interval_days = int(request.form['interval_days'])
            interval_time = request.form['interval_time']
            hour, minute = map(int, interval_time.split(':'))
            task.run_time = f'Every {interval_days} days at {interval_time}'
            task.interval_days = interval_days
            task.interval_time = interval_time
            # Вычисляем правильное время запуска
            now = datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если время уже прошло, добавляем один день
            if start_date <= now:
                start_date += timedelta(days=1)
            job_kwargs = {
                'id': task_id,
                'func': execute_task,
                'args': [task_id],
                'trigger': 'interval',
                'days': interval_days,
                'start_date': start_date
            }
            # Добавляем end_date если указан
            if end_date:
                job_kwargs['end_date'] = end_date
            scheduler.add_job(**job_kwargs)
            logging.info(f'Task {task_name} scheduled every {interval_days} days at {interval_time}' + (f' until {end_date}' if end_date else ''))

        db.session.add(task)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('create_task.html')

@app.route('/run/<task_id>')
def run_task(task_id):
    task = db.session.get(Task, task_id)
    if task:
        # Ручной запуск - передаем manual=True
        execute_task(task_id, manual=True)
        if task.is_active:
            flash(f'✅ Задача "{task.name}" успешно запущена!', 'success')
        else:
            flash(f'✅ Архивная задача "{task.name}" успешно запущена (остаётся в архиве)', 'success')
    return redirect(url_for('index'))

@app.route('/delete/<task_id>')
def delete_task(task_id):
    task = db.session.get(Task, task_id)
    if task:
        try:
            scheduler.remove_job(task_id)
        except JobLookupError:
            logging.warning(f'Job {task_id} not found in scheduler')

        try:
            os.remove(task.path)
        except FileNotFoundError:
            logging.warning(f'File for task {task_id} not found')

        # Удаляем историю задачи
        TaskHistory.query.filter_by(task_id=task_id).delete()
        # Удаляем саму задачу
        db.session.delete(task)
        db.session.commit()

        logging.info(f'Task {task_id} deleted')
    return redirect(url_for('index'))

@app.route('/archive/<task_id>')
def archive_task(task_id):
    """Архивирует задачу (переносит в неактивные)"""
    task = db.session.get(Task, task_id)
    if task:
        try:
            scheduler.remove_job(task_id)
        except JobLookupError:
            logging.warning(f'Job {task_id} not found in scheduler')

        task.is_active = False
        db.session.commit()
        logging.info(f'Task {task_id} archived')
    return redirect(url_for('index'))

@app.route('/unarchive/<task_id>')
def unarchive_task(task_id):
    """Разархивирует задачу (восстанавливает в активные)"""
    task = db.session.get(Task, task_id)
    if not task:
        return redirect(url_for('index'))

    # Восстанавливаем задачу в планировщике
    try:
        if task.schedule_type == 'once':
            # Проверяем, не прошло ли время выполнения
            run_time = datetime.strptime(task.run_time, '%Y-%m-%dT%H:%M')
            if run_time > datetime.now():
                # Время не прошло - восстанавливаем задачу
                task.is_active = True
                scheduler.add_job(
                    id=task.id,
                    func=execute_task,
                    args=[task.id],
                    trigger='date',
                    run_date=run_time
                )
                db.session.commit()
                logging.info(f'Task {task_id} unarchived')
                return redirect(url_for('index'))
            else:
                # Время прошло - перенаправляем на редактирование
                flash(f'⚠️ Время выполнения задачи "{task.name}" уже прошло. Укажите новое время для разархивации.', 'warning')
                logging.info(f'Task {task_id} unarchive attempt: time has passed, redirecting to edit')
                return redirect(url_for('edit_task', task_id=task_id))
        elif task.schedule_type == 'daily':
            task.is_active = True
            hour, minute = map(int, task.daily_time.split(':'))
            job_kwargs = {
                'id': task.id,
                'func': execute_task,
                'args': [task.id],
                'trigger': 'cron',
                'hour': hour,
                'minute': minute
            }
            # Добавляем end_date если указан
            if task.end_date:
                job_kwargs['end_date'] = task.end_date
            scheduler.add_job(**job_kwargs)
        elif task.schedule_type == 'interval':
            task.is_active = True
            hour, minute = map(int, task.interval_time.split(':'))
            now = datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start_date <= now:
                start_date += timedelta(days=1)
            job_kwargs = {
                'id': task.id,
                'func': execute_task,
                'args': [task.id],
                'trigger': 'interval',
                'days': task.interval_days,
                'start_date': start_date
            }
            # Добавляем end_date если указан
            if task.end_date:
                job_kwargs['end_date'] = task.end_date
            scheduler.add_job(**job_kwargs)
    except Exception as e:
        logging.error(f'Error unarchiving task {task_id}: {str(e)}')
        task.is_active = False

    db.session.commit()
    logging.info(f'Task {task_id} unarchived')
    return redirect(url_for('index'))

@app.route('/import', methods=['POST'])
def import_tasks():
    file = request.files['file']
    if file:
        data = json.load(file)
        for task_data in data:
            task = Task(
                id=task_data['id'],
                name=task_data['name'],
                status=task_data['status'],
                schedule_type=task_data['schedule_type'],
                run_time=task_data['run_time'],
                path=task_data['path']
            )
            with open(task.path, 'w') as f:
                f.write(task_data['code'])
            db.session.add(task)
            scheduler.add_job(id=task.id, func=execute_task, args=[task.id], 
                            trigger='date', run_date=task.run_time)
        db.session.commit()
        logging.info('Tasks imported successfully')
    return redirect(url_for('index'))

@app.route('/export')
def export_tasks():
    tasks = Task.query.all()
    export_data = [task.to_dict() for task in tasks]
    return jsonify(export_data)

@app.route('/edit/<task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Обновляем базовую информацию
        task.name = request.form['name']

        # Запоминаем, была ли задача в архиве
        was_archived = not task.is_active

        # Активируем задачу при редактировании (если она была в архиве)
        task.is_active = True

        # Обрабатываем end_date (опциональное поле)
        end_date = None
        end_date_str = request.form.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass  # Если формат неверный, оставляем старое значение
            if not end_date:
                # Если поле пустое, удаляем end_date
                task.end_date = None
            else:
                task.end_date = end_date
        else:
            task.end_date = None

        # Сохраняем новый код, удаляя лишние пустые строки
        code = request.form['code'].strip()  # Удаляем пробелы в начале и конце
        with open(task.path, 'w', newline='') as f:  # Добавляем newline=''
            f.write(code)

        # Получаем новый тип расписания
        new_schedule_type = request.form['schedule_type']

        # Если тип расписания изменился или параметры расписания обновились
        try:
            scheduler.remove_job(task_id)
        except JobLookupError:
            logging.warning(f'Job {task_id} not found in scheduler when updating')

        # Обновляем расписание
        if new_schedule_type == 'once':
            run_time = request.form.get('run_time', '').strip()
            if not run_time:
                flash('❌ Не указано время выполнения. Пожалуйста, выберите дату и время для однократной задачи.', 'error')
                return redirect(url_for('edit_task', task_id=task_id))

            task.run_time = run_time
            task.schedule_type = 'once'
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='date',
                run_date=run_time
            )
            logging.info(f'Task {task.name} rescheduled for {run_time}')

        elif new_schedule_type == 'daily':
            daily_time = request.form.get('daily_time', '').strip()
            if not daily_time:
                flash('❌ Не указано время выполнения. Пожалуйста, заполните поле времени для ежедневной задачи.', 'error')
                return redirect(url_for('edit_task', task_id=task_id))

            hour, minute = map(int, daily_time.split(':'))
            task.run_time = f'Daily at {daily_time}'
            task.schedule_type = 'daily'
            task.daily_time = daily_time
            job_kwargs = {
                'id': task_id,
                'func': execute_task,
                'args': [task_id],
                'trigger': 'cron',
                'hour': hour,
                'minute': minute
            }
            # Добавляем end_date если указан
            if task.end_date:
                job_kwargs['end_date'] = task.end_date
            scheduler.add_job(**job_kwargs)
            logging.info(f'Task {task.name} rescheduled daily at {daily_time}' + (f' until {task.end_date}' if task.end_date else ''))

        elif new_schedule_type == 'interval':
            interval_days_str = request.form.get('interval_days', '1').strip()
            if not interval_days_str:
                interval_days = 1
            else:
                try:
                    interval_days = int(interval_days_str)
                    if interval_days < 1:
                        interval_days = 1
                except ValueError:
                    interval_days = 1

            interval_time = request.form.get('interval_time', '').strip()
            if not interval_time:
                flash('❌ Не указано время выполнения. Пожалуйста, заполните поле времени для интервальной задачи.', 'error')
                return redirect(url_for('edit_task', task_id=task_id))

            hour, minute = map(int, interval_time.split(':'))
            task.run_time = f'Every {interval_days} days at {interval_time}'
            task.schedule_type = 'interval'
            task.interval_days = interval_days
            task.interval_time = interval_time
            # Вычисляем правильное время запуска
            now = datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если время уже прошло, добавляем один день
            if start_date <= now:
                start_date += timedelta(days=1)
            job_kwargs = {
                'id': task_id,
                'func': execute_task,
                'args': [task_id],
                'trigger': 'interval',
                'days': interval_days,
                'start_date': start_date
            }
            # Добавляем end_date если указан
            if task.end_date:
                job_kwargs['end_date'] = task.end_date
            scheduler.add_job(**job_kwargs)
            logging.info(f'Task {task.name} rescheduled every {interval_days} days at {interval_time}' + (f' until {task.end_date}' if task.end_date else ''))

        db.session.commit()

        # Проверяем, была ли задача в архиве до редактирования
        if was_archived:
            flash(f'✅ Задача "{task.name}" успешно активирована и восстановлена из архива!', 'success')
        else:
            flash(f'✅ Задача "{task.name}" успешно обновлена!', 'success')

        return redirect(url_for('index'))

    # Получаем код задачи для отображения в форме
    with open(task.path, 'r', newline='') as f:  # Добавляем newline=''
        code = f.read().strip()  # Удаляем лишние пробелы в начале и конце

    return render_template('edit_task.html', task=task, code=code)

def execute_task(task_id, manual=False):
    """
    Выполняет задачу.

    Args:
        task_id: ID задачи
        manual: True если запуск ручной, False если по расписанию
    """
    with app.app_context():
        task = db.session.get(Task, task_id)
        if not task:
            logging.error(f'Task {task_id} not found')
            return
            
        start_time = datetime.now()
        
        # Создаем запись в истории
        history_record = TaskHistory(
            task_id=task_id,
            task_name=task.name,
            start_time=start_time,
            status='Running'
        )
        db.session.add(history_record)
        db.session.commit()
        
        try:
            task.status = 'Running'
            # Используем newline='' при чтении
            with open(task.path, 'r', newline='') as f:
                code = f.read().strip()
            
            # Перенаправляем вывод в строку
            from io import StringIO
            import sys
            old_stdout = sys.stdout
            redirected_output = sys.stdout = StringIO()
            
            exec(code)
            
            # Восстанавливаем стандартный вывод и получаем результат
            sys.stdout = old_stdout
            output = redirected_output.getvalue()

            task.status = 'Completed'
            history_record.status = 'Completed'
            history_record.output = output
            logging.info(f'Task {task.name} completed successfully')

            # Автоматически архивируем одноразовые задачи только после выполнения по расписанию
            # При ручном запуске оставляем задачу в текущем состоянии
            if task.schedule_type == 'once' and not manual:
                task.is_active = False
                logging.info(f'Task {task.name} (once) archived after scheduled execution')
        except Exception as e:
            task.status = 'Failed'
            history_record.status = 'Failed'
            history_record.error = str(e)
            logging.error(f'Task {task.name} failed: {str(e)}')

        history_record.end_time = datetime.now()
        db.session.commit()

@app.route('/history/<task_id>')
def view_history(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return redirect(url_for('index'))
    
    # Получаем последние 100 записей из базы данных
    history = TaskHistory.query.filter_by(task_id=task_id)\
        .order_by(TaskHistory.start_time.desc())\
        .limit(100)\
        .all()
    
    return render_template('history.html', task=task, history=history)

def cleanup_old_history():
    # Удаляем записи старше 30 дней
    thirty_days_ago = datetime.now() - timedelta(days=30)
    TaskHistory.query.filter(TaskHistory.start_time < thirty_days_ago).delete()
    db.session.commit()

# Восстанавливаем задачи из БД при запуске (вызывается после определения execute_task)
if not app.config.get('TESTING'):
    restore_tasks()

if __name__ == '__main__':
    host = '127.0.0.1'
    port = 5000
    print(f' * Running on http://{host}:{port}/ (Press CTRL+C to quit)')
    app.run(debug=True, host=host, port=port)
