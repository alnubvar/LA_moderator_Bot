import logging
from aiogram import executor, types
from config import dp  
from handlers.message_handler import register_handlers

# Включаем логирование
logging.basicConfig(level=logging.INFO)

register_handlers(dp)

@dp.message_handler(commands=["start", "help"])
async def send_welcome(message: types.Message):
    await message.reply("Привет! Я бот-модератор. Пока умею только здороваться 😊")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
