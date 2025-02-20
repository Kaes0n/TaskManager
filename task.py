from flask import Flask, render_template, request, redirect, url_for, jsonify
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
VERSION = '1.0.0'  # Major.Minor.Patch
# История версий:
# 1.0.0 - Первая стабильная версия
#   - Создание, редактирование, удаление задач
#   - Поддержка разных типов расписания (однократное, ежедневное, интервальное)
#   - История выполнения задач
#   - Хранение данных в SQLite
#   - Автоматическое восстановление задач при перезапуске

app = Flask(__name__)
# Конфигурация базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///taskmanager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
            'interval_time': self.interval_time
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
                    scheduler.add_job(
                        id=task.id,
                        func=execute_task,
                        args=[task.id],
                        trigger='cron',
                        hour=hour,
                        minute=minute
                    )
                elif task.schedule_type == 'interval':
                    hour, minute = map(int, task.interval_time.split(':'))
                    scheduler.add_job(
                        id=task.id,
                        func=execute_task,
                        args=[task.id],
                        trigger='interval',
                        days=task.interval_days,
                        start_date=datetime.now().replace(hour=hour, minute=minute)
                    )
            except Exception as e:
                logging.error(f'Error restoring task {task.id}: {str(e)}')

# Инициализация планировщика
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.scheduler.add_jobstore(SQLAlchemyJobStore(url='sqlite:///jobs.sqlite'), 'default')

# Создаем таблицы и восстанавливаем задачи
with app.app_context():
    db.create_all()
    
# Запускаем планировщик после создания таблиц
scheduler.start()
restore_tasks()  # Восстанавливаем задачи после запуска планировщика

logging.basicConfig(filename='task_manager.log', level=logging.INFO, format='%(asctime)s - %(message)s')

TASKS_DIR = 'tasks'
os.makedirs(TASKS_DIR, exist_ok=True)

@app.route('/')
def index():
    tasks_list = Task.query.all()
    return render_template('index.html', tasks=tasks_list, version=VERSION)

@app.route('/create', methods=['GET', 'POST'])
def create_task():
    if request.method == 'POST':
        task_id = str(uuid.uuid4())
        task_name = request.form['name']
        # Удаляем лишние пробелы и переносы строк
        code = request.form['code'].strip()
        schedule_type = request.form['schedule_type']
        
        task_path = os.path.join(TASKS_DIR, f'{task_id}.py')
        # Используем newline='' при записи
        with open(task_path, 'w', newline='') as f:
            f.write(code)
        
        task = Task(
            id=task_id,
            name=task_name,
            status='Scheduled',
            schedule_type=schedule_type,
            path=task_path
        )

        if schedule_type == 'once':
            run_time = request.form['run_time']
            task.run_time = run_time
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
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='cron',
                hour=hour,
                minute=minute
            )
            logging.info(f'Task {task_name} scheduled daily at {daily_time}')

        elif schedule_type == 'interval':
            interval_days = int(request.form['interval_days'])
            interval_time = request.form['interval_time']
            hour, minute = map(int, interval_time.split(':'))
            task.run_time = f'Every {interval_days} days at {interval_time}'
            task.interval_days = interval_days
            task.interval_time = interval_time
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='interval',
                days=interval_days,
                start_date=datetime.now().replace(hour=hour, minute=minute)
            )
            logging.info(f'Task {task_name} scheduled every {interval_days} days at {interval_time}')

        db.session.add(task)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('create_task.html')

@app.route('/run/<task_id>')
def run_task(task_id):
    task = db.session.get(Task, task_id)
    if task:
        execute_task(task_id)
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
            run_time = request.form['run_time']
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
            daily_time = request.form['daily_time']
            hour, minute = map(int, daily_time.split(':'))
            task.run_time = f'Daily at {daily_time}'
            task.schedule_type = 'daily'
            task.daily_time = daily_time
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='cron',
                hour=hour,
                minute=minute
            )
            logging.info(f'Task {task.name} rescheduled daily at {daily_time}')

        elif new_schedule_type == 'interval':
            interval_days = int(request.form['interval_days'])
            interval_time = request.form['interval_time']
            hour, minute = map(int, interval_time.split(':'))
            task.run_time = f'Every {interval_days} days at {interval_time}'
            task.schedule_type = 'interval'
            task.interval_days = interval_days
            task.interval_time = interval_time
            scheduler.add_job(
                id=task_id,
                func=execute_task,
                args=[task_id],
                trigger='interval',
                days=interval_days,
                start_date=datetime.now().replace(hour=hour, minute=minute)
            )
            logging.info(f'Task {task.name} rescheduled every {interval_days} days at {interval_time}')
        
        db.session.commit()
        return redirect(url_for('index'))
    
    # Получаем код задачи для отображения в форме
    with open(task.path, 'r', newline='') as f:  # Добавляем newline=''
        code = f.read().strip()  # Удаляем лишние пробелы в начале и конце
    
    return render_template('edit_task.html', task=task, code=code)

def execute_task(task_id):
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


if __name__ == '__main__':
    host = '127.0.0.1'
    port = 5000
    print(f' * Running on http://{host}:{port}/ (Press CTRL+C to quit)')
    app.run(debug=True, host=host, port=port)
