import re
from aiogram import types
from aiogram.types import Message
from handlers.log_users import log_user
from data.message_logger import log_message
from utils.config_loader import load_config
from utils.db import add_or_update_user, mark_violator

config = load_config()
WHITELIST = config.get("whitelist", [])
BAD_PATTERNS = config.get("bad_patterns", [])


async def check_message(message: Message):
    user = message.from_user

    if user.username in WHITELIST or str(user.id) in WHITELIST:
        return

    text = message.text.lower() if message.text else ""

    for pattern in BAD_PATTERNS:
        if re.search(pattern, text):
            await message.delete()
            await message.answer(
                f"⛔️ @{user.username or user.full_name}, реклама запрещена. Для размещения — напишите админу @AdmLosAngel"
            )
            await mark_violator(user.id)  # обязательно await
            return


async def handle_message(message: Message):
    await add_or_update_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name,
        violator=False
    )
    await log_user(message)


def register_handlers(dp):
    dp.register_message_handler(check_message, content_types=types.ContentTypes.TEXT)
    dp.register_message_handler(handle_message, content_types=types.ContentTypes.TEXT)
