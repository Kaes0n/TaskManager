#!/usr/bin/env python3
"""
Скрипт миграции базы данных с версии 1.0.0 на 1.2.0

Добавляет новые поля в существующую базу данных без потери данных.
"""

import sqlite3
import os
import shutil
from datetime import datetime

def migrate_database(db_path='taskmanager.db', backup=True):
    """
    Выполняет миграцию базы данных до версии 1.2.0

    Args:
        db_path: Путь к файлу базы данных
        backup: Создавать ли резервную копию перед миграцией
    """

    if not os.path.exists(db_path):
        print(f"❌ База данных {db_path} не найдена!")
        return False

    # Создаем резервную копию
    if backup:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f"{db_path}.backup_{timestamp}"
        shutil.copy2(db_path, backup_path)
        print(f"✅ Создана резервная копия: {backup_path}")

    try:
        # Подключаемся к базе данных
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Проверяем, существует ли таблица tasks
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='tasks'
        """)
        table_exists = cursor.fetchone()

        if not table_exists:
            print("⚠️  Таблица tasks не найдена!")
            print("🔄 Создание таблицы tasks с полной структурой...")
            cursor.execute("""
                CREATE TABLE tasks (
                    id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    schedule_type VARCHAR(20) NOT NULL,
                    run_time VARCHAR(100) NOT NULL,
                    path VARCHAR(200) NOT NULL,
                    daily_time VARCHAR(10),
                    interval_days INTEGER,
                    interval_time VARCHAR(10),
                    is_active BOOLEAN DEFAULT 1 NOT NULL,
                    end_date DATETIME
                )
            """)
            print("✅ Таблица tasks создана с полной структурой версии 1.2.0")
            print("⚠️  ВНИМАНИЕ: Таблица была пустой, данные не перенесены")
        else:
            # Таблица существует - проверяем текущую версию схемы
            cursor.execute("PRAGMA table_info(tasks)")
            columns = [row[1] for row in cursor.fetchall()]

            print(f"📋 Текущие колонки в таблице tasks: {columns}")

            # Миграция 1.1.0: Добавляем поле is_active
            if 'is_active' not in columns:
                print("🔄 Миграция 1.1.0: Добавление поля is_active...")
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL
                """)
                # Обновляем существующие записи
                cursor.execute("UPDATE tasks SET is_active = 1 WHERE is_active IS NULL")
                print("✅ Поле is_active добавлено")
            else:
                print("ℹ️  Поле is_active уже существует")

            # Миграция 1.2.0: Добавляем поле end_date
            if 'end_date' not in columns:
                print("🔄 Миграция 1.2.0: Добавление поля end_date...")
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN end_date DATETIME
                """)
                print("✅ Поле end_date добавлено")
            else:
                print("ℹ️  Поле end_date уже существует")

        # Проверяем и добавляем таблицу alembic_version если нужно (для APScheduler)
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='alembic_version'
        """)
        if not cursor.fetchone():
            print("🔄 Создание таблицы alembic_version для APScheduler...")
            cursor.execute("""
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL
                )
            """)
            cursor.execute("""
                INSERT INTO alembic_version (version_num)
                VALUES ('1')
            """)
            print("✅ Таблица alembic_version создана")

        # Проверяем и добавляем таблицу apscheduler_jobs если нужно
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='apscheduler_jobs'
        """)
        if not cursor.fetchone():
            print("🔄 Создание таблицы apscheduler_jobs...")
            cursor.execute("""
                CREATE TABLE apscheduler_jobs (
                    id VARCHAR(191) NOT NULL,
                    next_run_time REAL,
                    job_state BLOB,
                    PRIMARY KEY (id)
                )
            """)
            print("✅ Таблица apscheduler_jobs создана")

        # Применяем изменения
        conn.commit()

        # Проверяем результат
        cursor.execute("PRAGMA table_info(tasks)")
        new_columns = [row[1] for row in cursor.fetchall()]
        print(f"\n📋 Новые колонки в таблице tasks: {new_columns}")

        # Показываем количество задач
        cursor.execute("SELECT COUNT(*) FROM tasks")
        task_count = cursor.fetchone()[0]
        print(f"📊 Всего задач в базе: {task_count}")

        conn.close()

        print("\n✅ Миграция успешно завершена!")
        print("🚀 Теперь можно запускать актуальную версию программы")

        return True

    except sqlite3.Error as e:
        print(f"❌ Ошибка при миграции: {e}")
        if backup and os.path.exists(backup_path):
            print(f"💡 Резервная копия сохранена: {backup_path}")
            restore = input("Хотите восстановить из резервной копии? (y/n): ")
            if restore.lower() == 'y':
                shutil.copy2(backup_path, db_path)
                print("✅ База данных восстановлена из резервной копии")
        return False


if __name__ == '__main__':
    import sys

    # Определяем путь к базе данных
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = 'taskmanager.db'

    print("=" * 60)
    print("Миграция базы данных TaskManager")
    print(f"Версия: 1.0.0 → 1.2.0")
    print(f"База данных: {db_path}")
    print("=" * 60)
    print()

    # Подтверждение
    response = input("Продолжить миграцию? (y/n): ")
    if response.lower() != 'y':
        print("❌ Миграция отменена")
        sys.exit(0)

    success = migrate_database(db_path)

    if not success:
        sys.exit(1)
