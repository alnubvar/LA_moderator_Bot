from aiogram import types
from aiogram.dispatcher.filters import Command
from aiogram.types import Message
from config import dp  
from handlers.log_users import log_user
from data.message_logger import log_message
from utils.config_loader import load_config
import re

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
            return

@dp.message_handler()
async def handle_message(message: types.Message):
    await log_user(message)

def register_handlers(dp):
    dp.register_message_handler(check_message, content_types=types.ContentTypes.TEXT)
