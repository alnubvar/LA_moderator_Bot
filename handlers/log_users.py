from aiogram import types
import json
import os

USERS_FILE = os.path.join("data", "users_seen.json")

async def log_user(message: types.Message):
    user_data = {
        "id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
    }

    users = []
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            try:
                users = json.load(f)
            except json.JSONDecodeError:
                pass  # файл пуст или кривой

    if not any(u["id"] == user_data["id"] for u in users):
        users.append(user_data)
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
