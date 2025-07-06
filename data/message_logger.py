import csv
import os
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(__file__), "messages.csv")

def log_message(user_id, username, message_text, chat_id):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Если файл пустой, записываем заголовки
    file_exists = os.path.isfile(LOG_FILE)
    is_empty = not file_exists or os.path.getsize(LOG_FILE) == 0

    with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if is_empty:
            writer.writerow(["timestamp", "user_id", "username", "chat_id", "message_text"])

        writer.writerow([
            datetime.now().isoformat(),
            user_id,
            username or "",
            chat_id,
            message_text.replace("\n", " ")
        ])
