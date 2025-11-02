import csv
import sqlite3
from pathlib import Path

# Путь к резервной копии
BACKUP_FILE = Path("data/backups/whitelist_backup_2025-10-21_23-45-12.csv")  # заменишь на актуальное имя файла
DB_PATH = Path("data/users.db")

# Подключаемся к базе
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Проверяем, что таблица whitelist существует
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whitelist';")
if not cur.fetchone():
    print("❌ Таблица whitelist не найдена. Сначала запусти бота, чтобы она создалась.")
    conn.close()
    exit()

# Импортируем данные из CSV
with open(BACKUP_FILE, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        username = row.get("username")
        until_date = row.get("until_date")
        if username:
            cur.execute("""
                INSERT OR IGNORE INTO whitelist (username, until_date)
                VALUES (?, ?)
            """, (username.lower(), until_date))
            count += 1

conn.commit()
conn.close()

print(f"✅ Успешно восстановлено {count} записей из {BACKUP_FILE.name}")
