# main.py
import os
import contextlib
import asyncio
import logging
import pytz
import subprocess
import sys
import threading
import aiohttp

from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from utils.config_loader import BOT_TOKEN
from utils.db import init_db
from utils.db import reset_all_limits
from utils.db import backup_db
from utils.db import cleanup_old_violations
from handlers import admin_commands, message_handler

if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# ── Логирование ──
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_log_path():
    """Путь к файлу логов за текущие сутки (по локальному времени)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.log"


LOG_FILE = get_log_path()
ERROR_LOG_FILE = LOG_DIR / "errors.log"

# Базовая настройка логов
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)s] | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# Отдельный файл для ошибок
error_handler = logging.FileHandler(ERROR_LOG_FILE, encoding="utf-8")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(
    logging.Formatter("%(asctime)s | [%(levelname)s] | %(name)s | %(message)s")
)

for logger_name in logging.root.manager.loggerDict:
    logging.getLogger(logger_name).addHandler(error_handler)

logger = logging.getLogger("LA_Moderator_Bot")


async def rotate_logs_daily():
    """Каждый день создаёт новый лог-файл в полночь по локальному времени."""
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_seconds = (next_midnight - now).total_seconds()
        await asyncio.sleep(sleep_seconds)

        new_path = get_log_path()
        # Закрываем старые файловые обработчики
        for handler in list(logging.getLogger().handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logging.getLogger().removeHandler(handler)

        # Подключаем новый лог-файл
        file_handler = logging.FileHandler(new_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | [%(levelname)s] | %(name)s | %(message)s")
        )
        logging.getLogger().addHandler(file_handler)

        logger.info(f"📁 Новый день — лог-файл переключён: {new_path}")


# ── Глобалы ──
storage = MemoryStorage()  # Временное хранилище FSM (для состояний)
bot: Bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp: Dispatcher = Dispatcher(bot, storage=storage)
shutdown_flag: asyncio.Event = asyncio.Event()


async def _autotrain_loop():
    logger = logging.getLogger("LA_Moderator_Bot")
    interval_sec = 5 * 24 * 60 * 60  # каждые 5 дней

    await asyncio.sleep(10)  # даём боту запуститься

    def run_autotrain():
        """Запускает обучение синхронно, но в отдельном потоке (чтобы не блокировать aiogram)."""
        try:
            logger.info("🧠 Автообучение: запускаю ml_autotrain.py ...")
            result = subprocess.run(
                [sys.executable, "ml_autotrain.py"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            if result.stdout:
                logger.info(result.stdout.strip())
            if result.stderr:
                logger.warning(result.stderr.strip())
            if result.returncode == 0:
                logger.info("✅ Автообучение завершено успешно.")
            else:
                logger.error(
                    f"❌ Автообучение завершилось с кодом {result.returncode}."
                )
        except Exception as e:
            logger.exception(f"💥 Ошибка автообучения: {e}")

    while True:
        # один поток на цикл, без зацикливания
        t = threading.Thread(target=run_autotrain, daemon=True)
        t.start()

        logger.info("⏳ Следующее автообучение через 5 дней.")
        await asyncio.sleep(interval_sec)


async def setup_main_menu(bot):
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    menu.row(KeyboardButton("📋 Статистика"), KeyboardButton("💼 Whitelist"))
    menu.row(
        KeyboardButton("➕ Добавить в Whitelist"),
        KeyboardButton("🗑 Удалить из Whitelist"),
    )
    menu.row(KeyboardButton("➕ Добавить чат"), KeyboardButton("⚙️ Настройки рекламы"))
    menu.row(KeyboardButton("👑 Добавить админа"), KeyboardButton("🗑 Удалить админа"))
    return menu


async def set_bot_menu():
    """Подсказки-команды в меню Telegram."""
    commands = [
        types.BotCommand(command="/start", description="👋 Приветствие"),
        types.BotCommand(command="/help", description="💡 Список команд и справка"),
        types.BotCommand(command="/status", description="📊 Статистика бота"),
        types.BotCommand(command="/whitelist", description="📋 Показать whitelist"),
        types.BotCommand(
            command="/whitelist_info", description="ℹ️ Проверить рекламодателя"
        ),
        types.BotCommand(
            command="/add_whitelist", description="➕ Добавить в whitelist"
        ),
        types.BotCommand(
            command="/remove_whitelist", description="🗑 Удалить из whitelist"
        ),
        types.BotCommand(command="/admins", description="👑 Показать список админов"),
        types.BotCommand(command="/add_admin", description="➕ Добавить нового админа"),
        types.BotCommand(command="/remove_admin", description="🗑 Удалить админа"),
        types.BotCommand(
            command="/reset_limits", description="🔁 Сбросить лимиты рекламодателей"
        ),
        types.BotCommand(command="/chats", description="💬 Список активных чатов"),
        types.BotCommand(command="/train_ml", description="🧠 Обучить ML-модель"),
        types.BotCommand(
            command="/ml_stats", description="📈 График F1-оценок ML-модели"
        ),
        types.BotCommand(command="/ads", description="💲 Узнать цены на рекламу"),
        types.BotCommand(command="/set_ads", description="⚙️ Изменить текст рекламы"),
        types.BotCommand(command="/shutdown", description="🛑 Остановить бота"),
    ]

    await bot.set_my_commands(commands)
    logger.info("✅ Команды меню установлены.")


async def watch_shutdown():
    """Следит за флагом и останавливает polling по /shutdown."""
    await shutdown_flag.wait()
    logger.warning("🛑 Получен сигнал выключения — останавливаем polling…")
    dp.stop_polling()  # корректно прерывает dp.start_polling()


async def reset_limits_daily():
    """Ежедневный сброс лимитов в 00:00 по Лос-Анджелесу."""
    la_tz = pytz.timezone("America/Los_Angeles")
    while True:
        now = datetime.now(la_tz)
        # Следующее наступление полуночи
        next_reset = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_time = (next_reset - now).total_seconds()

        await asyncio.sleep(sleep_time)
        reset_all_limits()
        logger.info(
            f"🔁 Автоматический сброс лимитов (временная зона LA, дата {datetime.now(la_tz).date()})"
        )


async def ping_telegram_api(bot: Bot):
    """
    Периодически пингует Telegram API, чтобы отслеживать обрывы соединения.
    Работает в фоне, не мешает polling.
    """
    la_tz = pytz.timezone("America/Los_Angeles")

    while True:
        try:
            # Пытаемся получить базовую информацию о боте (ping)
            me = await bot.get_me()
            logger.info(
                f"📡 Ping OK — бот на связи как @{me.username} ({datetime.now(la_tz).strftime('%H:%M:%S %Z')})"
            )
        except aiohttp.ClientError as e:
            logger.warning(f"⚠️ Ping failed: {e} — потеряно соединение с Telegram API.")
        except Exception as e:
            logger.error(f"💥 Ошибка при пинге Telegram API: {e}", exc_info=True)

        # повторяем каждый час
        await asyncio.sleep(3600)


async def run_bot():
    shutdown_flag.clear()
    logger.info("🚀 Запуск LA_Moderator_Bot...")

    # 1) Подготовка БД (внутри init_db уже есть резервная копия и миграции)
    init_db()
    logger.info("✅ Все таблицы проверены, структура базы актуальна.")

    # === Фоновые задачи для обслуживания базы ===
    async def periodic_backup():
        """Создаёт резервную копию базы каждые 24 часа."""
        while True:
            backup_db()
            await asyncio.sleep(24 * 60 * 60)  # 1 раз в сутки

    async def periodic_cleanup():
        """Очищает старые нарушения (старше 90 дней)."""
        while True:
            cleanup_old_violations(90)
            await asyncio.sleep(24 * 60 * 60)  # 1 раз в сутки

    # Запускаем фоновые процессы
    asyncio.create_task(periodic_backup())
    asyncio.create_task(periodic_cleanup())

    # 2) Регистрация хендлеров (ВАЖНО: никакого @dp в модулях)
    admin_commands.register_admin_commands(dp, shutdown_flag)
    message_handler.register_handlers(dp)

    # 3) Меню-команды
    await set_bot_menu()

    # 4) Запуск сторожа выключения и polling
    asyncio.get_event_loop().create_task(_autotrain_loop())

    # Фоновая задача: ежедневная ротация логов
    asyncio.create_task(rotate_logs_daily())

    # Фоновая задача: проверка соединения с Telegram API
    asyncio.create_task(ping_telegram_api(bot))

    watcher_task = asyncio.create_task(watch_shutdown())

    # 5) Основной цикл polling с авто-перезапуском при обрывах
    while True:
        try:
            logger.info("✅ Подключаемся к Telegram API...")

            # Запуск фоновой задачи автосброса лимитов (в LA-времени)
            asyncio.create_task(reset_limits_daily())

            # съедаем старые апдейты (например “зависший” /shutdown)
            await dp.skip_updates()

            menu = await setup_main_menu(bot)

            # запускаем polling
            logger.info("🚦 Запуск polling (ожидание сообщений от Telegram)...")
            await dp.start_polling()

        except Exception as e:
            logger.error(f"💥 Ошибка в polling: {e}", exc_info=True)
            logger.warning("♻️ Перезапуск polling через 15 секунд...")
            await asyncio.sleep(15)
            continue  # повторно перезапускаем цикл

        finally:
            # аккуратно останавливаем сторожа
            if not watcher_task.done():
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task

            # Дадим задачам дофлашиться
            await asyncio.sleep(0)

            # Закрываем HTTP-сессию aiogram
            try:
                session = await bot.get_session()
                await session.close()
            except Exception:
                with contextlib.suppress(Exception):
                    await bot.session.close()

            # Финализируем генераторы (приглушает редкие ворнинги)
            with contextlib.suppress(Exception):
                await asyncio.get_running_loop().shutdown_asyncgens()

            logger.info("✅ Бот корректно завершил работу.")
            break  # чтобы выйти из цикла, если shutdown_flag установлен


# ── Точка входа ──
if __name__ == "__main__":
    import contextlib

    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот остановлен вручную.")
