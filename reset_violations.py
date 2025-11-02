# reset_violations.py
import sqlite3
from pathlib import Path

DB_PATH = Path("data/users.db")

def reset_user_violations(username: str, chat_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    username = username.lstrip("@").lower()

    # Удаляем нарушения по пользователю
    if chat_id:
        cur.execute(
            "DELETE FROM violations WHERE LOWER(username) = ? AND chat_id = ?",
            (username, chat_id),
        )
        print(f"🧹 Нарушения @{username} удалены для чата {chat_id}.")
    else:
        cur.execute(
            "DELETE FROM violations WHERE LOWER(username) = ?",
            (username,),
        )
        print(f"🧹 Нарушения @{username} удалены во всех чатах.")

    # Сбрасываем статус нарушителя в таблице users
    cur.execute(
        "UPDATE users SET is_violator = 0, violation_count = 0 WHERE LOWER(username) = ?",
        (username,),
    )

    conn.commit()
    conn.close()

    print(f"✅ Пользователь @{username} сброшен и очищен из нарушителей.")


if __name__ == "__main__":
    # === Настрой здесь свои данные ===
    # username без @
    target_user = "alnub_work"       # ← заменяй на имя своего тест-аккаунта
    # chat_id можно оставить None, чтобы сбросить везде
    target_chat_id = None             # или, например: -1002007546590

    reset_user_violations(target_user, target_chat_id)
