import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "users.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_violator INTEGER DEFAULT 0
        )"""
    )
    conn.commit()
    conn.close()

def add_or_update_user(user_id, username, first_name, last_name, violator=False):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO users (id, username, first_name, last_name, is_violator)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            is_violator = is_violator OR ?
        """,
        (user_id, username, first_name, last_name, int(violator), int(violator))
    )
    conn.commit()
    conn.close()

def mark_violator(user_id):
    conn = get_connection()
    conn.execute("UPDATE users SET is_violator = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
