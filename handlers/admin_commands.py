# handlers/admin_commands.py
import os
import time
import logging
import asyncio
import subprocess
import sys
import traceback
import threading
import json
import matplotlib.pyplot as plt
from io import BytesIO
from main import AUTOTRAIN_PATH
from aiogram.types import InputFile
from datetime import datetime
from pathlib import Path
from aiogram import types
from aiogram.dispatcher import Dispatcher, FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from utils.db import (
    reset_all_limits,
    add_to_whitelist,
    remove_from_whitelist,
    get_whitelist,
    get_connection,
    add_admin,
    remove_admin,
    get_admins,
    get_all_chats,
)
from utils.config_loader import CONFIG
from main import setup_main_menu

logger = logging.getLogger("LA_Moderator_Bot")

ADS_FILE = "data/ads_text.txt"

# --- Админ-настройки ---
ADMIN_USERNAMES = set(u.lower() for u in CONFIG.get("admin_usernames", []))
ADMIN_CHAT_IDS = {580759300, 7773812278, 1047049705}

LOG_PATH = Path("data/ml_autotrain.log")

HISTORY_PATH = Path("data/ml/f1_history.json")


# --- Состояния FSM ---
class AdsEditState(StatesGroup):
    waiting_for_text = State()


class RemoveWhitelistState(StatesGroup):
    waiting_for_chat = State()
    waiting_for_username = State()


class WhitelistInfoState(StatesGroup):
    waiting_for_chat = State()
    waiting_for_username = State()


class ShowWhitelistState(StatesGroup):
    waiting_for_chat = State()


# === FSM состояния для админов ===
class AddAdminState(StatesGroup):
    waiting_for_username = State()


class RemoveAdminState(StatesGroup):
    waiting_for_username = State()


class AddWhitelistState(StatesGroup):
    waiting_for_chat = State()
    waiting_for_username = State()
    waiting_for_days = State()


# --- Проверка прав ---
def _is_admin(username: str, user_id: int) -> bool:
    uname = (username or "").lower()
    if uname and not uname.startswith("@"):
        uname = "@" + uname
    return (uname in ADMIN_USERNAMES) or (user_id in ADMIN_CHAT_IDS)


async def train_ml_command(message: types.Message):
    """Команда /train_ml — запускает автообучение ML модели."""
    user = message.from_user

    # --- Проверка прав доступа ---
    if not user:
        return
    if user.id not in ADMIN_CHAT_IDS and (
        not user.username or user.username.lower() not in ADMIN_USERNAMES
    ):
        await message.reply("🚫 У вас нет прав на запуск обучения.")
        return

    script_path = AUTOTRAIN_PATH  # ← ВАЖНО! Берём правильный путь из main.py

    if not script_path.exists():
        await message.reply(
            f"❌ Не найден файл ml_autotrain.py\nПуть: <code>{script_path}</code>",
            parse_mode="HTML",
        )
        return

    await message.reply("🧠 Запускаю обучение модели... ⏳")

    loop = asyncio.get_running_loop()

    def run_train():
        try:
            start_time = time.time()

            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            duration = time.time() - start_time

            if result.returncode == 0:
                # читаем последние строки лога
                if LOG_PATH.exists():
                    log_tail = LOG_PATH.read_text(
                        encoding="utf-8", errors="ignore"
                    ).splitlines()[-20:]
                    log_text = "\n".join(log_tail)
                else:
                    log_text = "⚠️ Лог не найден."

                text = (
                    "✅ Обучение завершено успешно.\n"
                    f"⏱ Время: {duration:.2f} сек.\n\n"
                    f"<pre>{log_text}</pre>"
                )
                asyncio.run_coroutine_threadsafe(
                    message.reply(text, parse_mode="HTML"), loop
                )
            else:
                err = result.stderr or "Неизвестная ошибка"
                asyncio.run_coroutine_threadsafe(
                    message.reply(
                        f"❌ Ошибка обучения (код {result.returncode}):\n<pre>{err[-1000:]}</pre>",
                        parse_mode="HTML",
                    ),
                    loop,
                )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                message.reply(f"💥 Ошибка запуска обучения: {e}"),
                loop,
            )

    threading.Thread(target=run_train, daemon=True).start()


def register_admin_commands(dp: Dispatcher, shutdown_flag: asyncio.Event):
    """
    Регистрируем все админские и служебные команды на переданный dp.
    ВНИМАНИЕ: никаких декораторов @dp.message_handler в модуле больше нет!
    """

    # ========== ОСНОВНЫЕ КОМАНДЫ ==========

    async def reset_limits_cmd(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ У вас нет прав для выполнения этой команды.")
            return
        reset_all_limits()
        await message.answer("✅ Лимиты рекламодателей успешно сброшены!")
        logger.info(
            f"🔁 Админ {message.from_user.username} сбросил лимиты рекламодателей."
        )

    async def ml_stats(message: types.Message):
        """Показывает график изменения F1-оценки ML-модели по датам."""
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ У вас нет прав.")
            return

        if not HISTORY_PATH.exists():
            await message.answer("📭 История обучений пока пуста.")
            return

        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            await message.answer(f"⚠️ Ошибка чтения истории: {e}")
            return

        if not data:
            await message.answer("📭 История обучений пуста.")
            return

        dates = [entry["date"] for entry in data]
        f1_scores = [entry["f1"] for entry in data]

        plt.figure(figsize=(6, 3))
        plt.plot(dates, f1_scores, marker="o", color="blue", linewidth=2)
        plt.title("F1-оценка модели по датам", fontsize=12)
        plt.xlabel("Дата обучения")
        plt.ylabel("F1-score")
        plt.xticks(rotation=45, ha="right")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.tight_layout()

        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()

        caption = f"📈 История F1-оценок ({len(f1_scores)} обучений)\nПоследняя: {f1_scores[-1]:.3f}"
        await message.answer_photo(
            photo=InputFile(buf, filename="f1_metrics.png"), caption=caption
        )

    # --- SHOW (перс-чатный Whitelist) ---

    async def show_whitelist(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        chats = get_all_chats()
        if not chats:
            await message.answer("📭 Бот пока не добавлен ни в один чат.")
            return

        text = "💬 <b>Из какого чата показать whitelist рекламодателей?</b>\n\n"
        for idx, (chat_id, title, added_at) in enumerate(chats, 1):
            text += f"{idx}. <b>{title}</b>\n   <code>{chat_id}</code>\n\n"

        await message.answer(
            text + "✍️ Отправьте <b>номер</b> чата из списка:", parse_mode="HTML"
        )
        await ShowWhitelistState.waiting_for_chat.set()

    async def show_whitelist_get_chat(message: types.Message, state: FSMContext):
        try:
            choice = int(message.text.strip())
        except ValueError:
            await message.answer("❗ Введите число (номер чата из списка).")
            return

        chats = get_all_chats()
        if not (1 <= choice <= len(chats)):
            await message.answer("❗ Неверный номер. Попробуйте снова.")
            return

        chat_id, title, _ = chats[choice - 1]

        from utils.db import get_whitelist

        rows = get_whitelist(chat_id)
        if not rows:
            await message.answer(
                f"📭 В чате <b>{title}</b> whitelist пуст.", parse_mode="HTML"
            )
            await state.finish()
            return

        msg = f"📋 <b>Whitelist рекламодателей в чате {title}:</b>\n\n"
        for r in rows:
            until = r["until_date"] or "∞"
            msg += f"• @{r['username']} — до <code>{until}</code>\n"
        await message.answer(msg, parse_mode="HTML")

        logger.info(
            f"📋 Админ {message.from_user.username} просмотрел whitelist чата {title} ({chat_id})."
        )
        await state.finish()

    async def show_status(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ У вас нет прав для выполнения этой команды.")
            return

        conn = get_connection()
        cur = conn.cursor()
        total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        violators = cur.execute(
            "SELECT COUNT(*) FROM users WHERE is_violator = 1"
        ).fetchone()[0]
        whitelist_cnt = cur.execute("SELECT COUNT(*) FROM whitelist").fetchone()[0]
        messages_cnt = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()

        text = (
            "📊 <b>Статистика MODERATOR</b>\n\n"
            f"👥 Пользователей в базе: <b>{total_users}</b>\n"
            f"🚫 Нарушителей: <b>{violators}</b>\n"
            f"💼 Активных рекламодателей: <b>{whitelist_cnt}</b>\n"
            f"💬 Сообщений в базе: <b>{messages_cnt}</b>\n\n"
            "🕒 Статистика обновляется в реальном времени."
        )
        await message.answer(text, parse_mode="HTML")
        logger.info(f"📊 Админ {message.from_user.username} запросил статистику.")

    async def show_help(message: types.Message):
        text = (
            "🤖 <b>Команды MODERATOR</b>\n\n"
            "👥 <b>Общие:</b>\n"
            "• /start — Запуск / приветствие\n"
            "• /help — Список команд и справка\n"
            "• /ads — Узнать цены на рекламу\n\n"
            "⚙️ <b>Админ:</b>\n"
            "• /add_whitelist — Добавить рекламодателя (пошагово)\n"
            "• /remove_whitelist — Удалить из whitelist (пошагово)\n"
            "• /whitelist_info — Проверить рекламодателя (пошагово)\n"
            "• /whitelist — Показать текущий whitelist\n"
            "• /admins — Показать список админов\n"
            "• /add_admin — Добавить нового админа (пошагово)\n"
            "• /remove_admin — Удалить админа (пошагово)\n"
            "• /reset_limits — Сбросить суточные лимиты\n"
            "• /set_ads — Изменить текст рекламы (через диалог)\n"
            "• /status — Показать статистику\n"
            "• /chats — Показать чаты, где работает бот\n"
            "• /train_ml — Обучить ML-модель\n"
            "• /ml_stats — График качества ML-модели (F1)\n"
            "• /shutdown — Корректно остановить бота\n\n"
            "🧹 Бот автоматически удаляет спам, контролирует рекламу и ведёт логи.\n\n"
            "💬 По вопросам рекламы — @AdmLosAngel"
        )

        await message.answer(text, parse_mode="HTML")

    async def shutdown_cmd(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ У вас нет прав для этой команды.")
            return
        await message.answer("🛑 Бот завершает работу по команде администратора...")
        logger.warning(f"🛑 Бот выключен администратором @{message.from_user.username}")
        shutdown_flag.set()

    # ========== WHITELIST FSM ==========

    # --- ADD (перс-чатный Whitelist) ---
    async def add_whitelist_start(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        chats = get_all_chats()
        if not chats:
            await message.answer("📭 Бот пока не добавлен ни в один чат.")
            return

        text = "💬 <b>Выберите чат, куда добавить рекламодателя:</b>\n\n"
        for idx, (chat_id, title, added_at) in enumerate(chats, 1):
            text += f"{idx}. <b>{title}</b>\n   <code>{chat_id}</code>\n\n"

        await message.answer(
            text + "✍️ Отправьте <b>номер</b> чата из списка:", parse_mode="HTML"
        )
        await AddWhitelistState.waiting_for_chat.set()

    async def add_whitelist_get_chat(message: types.Message, state: FSMContext):
        try:
            choice = int(message.text.strip())
        except ValueError:
            await message.answer("❗ Введите число (номер чата из списка).")
            return

        chats = get_all_chats()
        if not (1 <= choice <= len(chats)):
            await message.answer("❗ Неверный номер. Попробуйте снова.")
            return

        chat_id, title, _ = chats[choice - 1]
        await state.update_data(chat_id=chat_id, chat_title=title)
        await message.answer(
            f"✅ Вы выбрали чат: <b>{title}</b>\n\nТеперь отправьте @username рекламодателя:",
            parse_mode="HTML",
        )
        await AddWhitelistState.waiting_for_username.set()

    async def add_whitelist_get_username(message: types.Message, state: FSMContext):
        username = message.text.strip().lstrip("@")
        await state.update_data(username=username)
        await message.answer("📅 На сколько дней добавить пользователя?")
        await AddWhitelistState.waiting_for_days.set()

    async def add_whitelist_get_days(message: types.Message, state: FSMContext):
        try:
            days = int(message.text.strip())
        except ValueError:
            await message.answer("❗ Введите число (например, 30).")
            return

        data = await state.get_data()
        username = data["username"]
        chat_id = data["chat_id"]
        chat_title = data["chat_title"]

        add_to_whitelist(chat_id, username, days)
        await message.answer(
            f"✅ Пользователь @{username} добавлен в whitelist чата <b>{chat_title}</b> на {days} дней.",
            parse_mode="HTML",
        )
        logger.info(
            f"👑 Админ {message.from_user.username} добавил @{username} в whitelist чата {chat_title} ({chat_id}) на {days} дней."
        )
        await state.finish()

    # --- REMOVE (перс-чатный Whitelist) ---
    async def remove_whitelist_start(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        chats = get_all_chats()
        if not chats:
            await message.answer("📭 Бот пока не добавлен ни в один чат.")
            return

        text = "💬 <b>Из какого чата удалить рекламодателя?</b>\n\n"
        for idx, (chat_id, title, added_at) in enumerate(chats, 1):
            text += f"{idx}. <b>{title}</b>\n   <code>{chat_id}</code>\n\n"

        await message.answer(
            text + "✍️ Отправьте <b>номер</b> чата из списка:", parse_mode="HTML"
        )
        await RemoveWhitelistState.waiting_for_chat.set()

    async def remove_whitelist_get_chat(message: types.Message, state: FSMContext):
        try:
            choice = int(message.text.strip())
        except ValueError:
            await message.answer("❗ Введите число (номер чата из списка).")
            return

        chats = get_all_chats()
        if not (1 <= choice <= len(chats)):
            await message.answer("❗ Неверный номер. Попробуйте снова.")
            return

        chat_id, title, _ = chats[choice - 1]
        await state.update_data(chat_id=chat_id, chat_title=title)
        await message.answer(
            f"✅ Вы выбрали чат: <b>{title}</b>\n\nТеперь отправьте @username рекламодателя, которого нужно удалить:",
            parse_mode="HTML",
        )
        await RemoveWhitelistState.waiting_for_username.set()

    async def remove_whitelist_get_username(message: types.Message, state: FSMContext):
        data = await state.get_data()
        chat_id = data["chat_id"]
        chat_title = data["chat_title"]
        username = message.text.strip().lstrip("@").lower()

        from utils.db import remove_from_whitelist, in_whitelist

        if not in_whitelist(chat_id, username):
            await message.answer(
                f"❌ Пользователь @{username} не найден в whitelist чата <b>{chat_title}</b>.",
                parse_mode="HTML",
            )
            await state.finish()
            return

        remove_from_whitelist(chat_id, username)
        await message.answer(
            f"🗑 Пользователь @{username} удалён из whitelist чата <b>{chat_title}</b>.",
            parse_mode="HTML",
        )
        logger.info(
            f"🗑 @{username} удалён из whitelist чата {chat_title} ({chat_id}) админом {message.from_user.username}."
        )
        await state.finish()

    # --- INFO (перс-чатный) ---
    async def whitelist_info_start(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        chats = get_all_chats()
        if not chats:
            await message.answer("📭 Бот пока не добавлен ни в один чат.")
            return

        text = "💬 <b>Из какого чата проверить рекламодателя?</b>\n\n"
        for idx, (chat_id, title, added_at) in enumerate(chats, 1):
            text += f"{idx}. <b>{title}</b>\n   <code>{chat_id}</code>\n\n"

        await message.answer(
            text + "✍️ Отправьте <b>номер</b> чата из списка:", parse_mode="HTML"
        )
        await WhitelistInfoState.waiting_for_chat.set()

    async def whitelist_info_get_chat(message: types.Message, state: FSMContext):
        try:
            choice = int(message.text.strip())
        except ValueError:
            await message.answer("❗ Введите число (номер чата из списка).")
            return

        chats = get_all_chats()
        if not (1 <= choice <= len(chats)):
            await message.answer("❗ Неверный номер. Попробуйте снова.")
            return

        chat_id, title, _ = chats[choice - 1]
        await state.update_data(chat_id=chat_id, chat_title=title)
        await message.answer(
            f"✅ Вы выбрали чат: <b>{title}</b>\n\nТеперь отправьте @username рекламодателя для проверки:",
            parse_mode="HTML",
        )
        await WhitelistInfoState.waiting_for_username.set()

    async def whitelist_info_show(message: types.Message, state: FSMContext):
        data = await state.get_data()
        chat_id = data["chat_id"]
        chat_title = data["chat_title"]
        username = message.text.strip().lstrip("@").lower()

        from utils.db import in_whitelist, get_ad_count

        if not in_whitelist(chat_id, username):
            await message.answer(
                f"❌ Пользователь @{username} не найден в whitelist чата <b>{chat_title}</b>.",
                parse_mode="HTML",
            )
            await state.finish()
            return

        # получаем дату окончания
        conn = get_connection()
        row = conn.execute(
            "SELECT until_date FROM whitelist WHERE chat_id = ? AND LOWER(username) = ?",
            (chat_id, username),
        ).fetchone()
        conn.close()

        until = row["until_date"] if row else None
        active = False
        if until:
            try:
                active = (
                    datetime.strptime(until, "%Y-%m-%d").date()
                    >= datetime.utcnow().date()
                )
            except Exception:
                pass

        status = "✅ Активен" if active else "⏰ Истёк"

        # проверяем лимиты рекламы
        conn = get_connection()
        user_row = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = ?", (username,)
        ).fetchone()
        conn.close()

        if user_row:
            user_id = user_row["id"]
            ad_count, daily_limit = get_ad_count(chat_id, user_id)
        else:
            ad_count, daily_limit = 0, 4

        msg = (
            f"📊 <b>Информация о @{username}</b>\n\n"
            f"💬 Чат: <b>{chat_title}</b>\n"
            f"📅 Активен до: <b>{until or '∞'}</b> ({status})\n"
            f"📣 Реклам сегодня: <b>{ad_count}</b> / {daily_limit}\n"
        )

        await message.answer(msg, parse_mode="HTML")
        logger.info(
            f"📊 Админ {message.from_user.username} проверил @{username} в whitelist чата {chat_title} ({chat_id})."
        )
        await state.finish()

    # ========== РЕКЛАМА ==========
    async def show_ads(message: types.Message):
        if not os.path.exists(ADS_FILE):
            await message.answer("📭 Текст рекламы пока не задан.")
            return
        with open(ADS_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
        await message.answer(text)

    async def set_ads(message: types.Message, state: FSMContext):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return
        await message.answer(
            "📝 На какой текст вы хотите заменить текущую рекламу?\nОтправьте новый текст 👇"
        )
        await AdsEditState.waiting_for_text.set()

    async def process_new_ads_text(message: types.Message, state: FSMContext):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            await state.finish()
            return
        new_text = message.text.strip()
        os.makedirs("data", exist_ok=True)
        with open(ADS_FILE, "w", encoding="utf-8") as f:
            f.write(new_text)
        await message.answer("✅ Текст рекламы успешно обновлён!")
        logger.info(
            f"💬 Админ {message.from_user.username} обновил рекламное сообщение."
        )
        await state.finish()

    # ========== START ==========
    async def cmd_start(message: types.Message):
        menu = await setup_main_menu(message.bot)
        await message.answer(
            "👋 Привет! Я LA Moderator Bot.\nВыберите действие из меню 👇",
            reply_markup=menu,
        )

    # --- Показать всех админов ---
    async def show_admins(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        db_admins = get_admins()
        cfg_admins = [a.lower() for a in CONFIG.get("admin_usernames", [])]

        msg = "👑 <b>Текущие администраторы:</b>\n\n"
        if cfg_admins:
            msg += "📦 Из config.json:\n"
            for a in cfg_admins:
                msg += f"• {a}\n"
        if db_admins:
            msg += "\n💾 Из базы данных:\n"
            for a in db_admins:
                msg += f"• {a}\n"
        if not cfg_admins and not db_admins:
            msg += "— пока нет администраторов —"

        await message.answer(msg, parse_mode="HTML")

    # --- Добавить админа ---
    async def add_admin_start(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return
        await message.answer(
            "👤 Введите @username пользователя, которого нужно добавить в админы:"
        )
        await AddAdminState.waiting_for_username.set()

    async def add_admin_process(message: types.Message, state: FSMContext):
        uname = message.text.strip().lower()
        if not uname.startswith("@"):
            uname = "@" + uname
        add_admin(uname, added_by=message.from_user.username)
        await message.answer(f"✅ Пользователь {uname} добавлен в список админов.")
        await state.finish()

    # --- Удалить админа ---
    async def remove_admin_start(message: types.Message):
        if not _is_admin(message.from_user.username, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return
        await message.answer(
            "👤 Введите @username пользователя, которого нужно удалить из админов:"
        )
        await RemoveAdminState.waiting_for_username.set()

    async def remove_admin_process(message: types.Message, state: FSMContext):
        uname = message.text.strip().lower()
        if not uname.startswith("@"):
            uname = "@" + uname
        remove_admin(uname)
        await message.answer(f"🗑 Пользователь {uname} удалён из списка админов.")
        await state.finish()

    async def add_chat_button(message: types.Message):
        # 🔒 Проверка прав
        if message.from_user.id not in ADMIN_CHAT_IDS and (
            not message.from_user.username
            or message.from_user.username.lower() not in ADMIN_USERNAMES
        ):
            await message.reply(
                "🚫 Только администраторы могут добавлять бота в новые чаты."
            )
            return

        bot_info = await message.bot.get_me()
        bot_username = bot_info.username
        invite_link = f"https://t.me/{bot_username}?startgroup=true"

        await message.reply(
            f"👋 Чтобы добавить <b>{bot_username}</b> в новый чат, просто перейди по ссылке:\n\n"
            f"🔗 <b>{invite_link}</b>\n\n"
            f"После добавления бот автоматически начнёт работать в чате ✅",
            parse_mode="HTML",
        )

    async def show_chats(message: types.Message):
        # 🔒 Проверка прав
        if message.from_user.id not in ADMIN_CHAT_IDS and (
            not message.from_user.username
            or message.from_user.username.lower() not in ADMIN_USERNAMES
        ):
            await message.reply("🚫 У вас нет прав для использования этой команды.")
            return

        chats = get_all_chats()
        if not chats:
            await message.reply("📭 Бот пока не добавлен ни в один чат.")
            return

        text = "💬 <b>Список активных чатов:</b>\n\n"
        for chat_id, title, added_at in chats:
            text += f"• <b>{title}</b>\n   <code>{chat_id}</code>\n🕒 {added_at}\n\n"

        await message.reply(text, parse_mode="HTML")

    # ========== РЕГИСТРАЦИЯ ==========
    dp.register_message_handler(cmd_start, commands=["start"])
    dp.register_message_handler(show_help, commands=["help"])
    dp.register_message_handler(show_status, commands=["status"])
    dp.register_message_handler(show_whitelist, commands=["whitelist"])
    dp.register_message_handler(reset_limits_cmd, commands=["reset_limits"])
    dp.register_message_handler(shutdown_cmd, commands=["shutdown"])
    dp.register_message_handler(show_ads, commands=["ads"])
    dp.register_message_handler(set_ads, commands=["set_ads"], state="*")
    dp.register_message_handler(
        process_new_ads_text, state=AdsEditState.waiting_for_text
    )
    dp.register_message_handler(show_admins, commands=["admins"])
    dp.register_message_handler(add_admin_start, commands=["add_admin"], state="*")
    dp.register_message_handler(train_ml_command, commands=["train_ml"])
    dp.register_message_handler(ml_stats, commands=["ml_stats"])
    dp.register_message_handler(show_chats, commands=["chats"])

    # FSM команды
    dp.register_message_handler(
        add_whitelist_start, commands=["add_whitelist"], state="*"
    )

    dp.register_message_handler(
        add_whitelist_get_chat, state=AddWhitelistState.waiting_for_chat
    )

    dp.register_message_handler(
        add_whitelist_get_username, state=AddWhitelistState.waiting_for_username
    )
    dp.register_message_handler(
        add_whitelist_get_days, state=AddWhitelistState.waiting_for_days
    )
    dp.register_message_handler(
        remove_whitelist_start, commands=["remove_whitelist"], state="*"
    )
    dp.register_message_handler(
        remove_whitelist_get_chat, state=RemoveWhitelistState.waiting_for_chat
    )
    dp.register_message_handler(
        remove_whitelist_get_username, state=RemoveWhitelistState.waiting_for_username
    )

    dp.register_message_handler(
        whitelist_info_start, commands=["whitelist_info"], state="*"
    )

    dp.register_message_handler(
        whitelist_info_get_chat, state=WhitelistInfoState.waiting_for_chat
    )
    dp.register_message_handler(
        whitelist_info_show, state=WhitelistInfoState.waiting_for_username
    )
    dp.register_message_handler(
        show_whitelist_get_chat, state=ShowWhitelistState.waiting_for_chat
    )

    dp.register_message_handler(
        add_admin_process, state=AddAdminState.waiting_for_username
    )
    dp.register_message_handler(
        remove_admin_start, commands=["remove_admin"], state="*"
    )
    dp.register_message_handler(
        remove_admin_process, state=RemoveAdminState.waiting_for_username
    )

    # --- Обработка кнопок из меню ---
    dp.register_message_handler(show_status, lambda m: m.text == "📋 Статистика")
    dp.register_message_handler(show_whitelist, lambda m: m.text == "💼 Whitelist")
    dp.register_message_handler(
        add_whitelist_start, lambda m: m.text == "➕ Добавить в Whitelist", state="*"
    )
    dp.register_message_handler(
        remove_whitelist_start, lambda m: m.text == "🗑 Удалить из Whitelist", state="*"
    )
    dp.register_message_handler(
        whitelist_info_start, lambda m: m.text == "ℹ️ Проверить рекламодателя", state="*"
    )
    dp.register_message_handler(show_ads, lambda m: m.text == "💬 Реклама")
    dp.register_message_handler(
        set_ads, lambda m: m.text == "⚙️ Настройки рекламы", state="*"
    )
    dp.register_message_handler(
        reset_limits_cmd, lambda m: m.text == "🧾 Сброс лимитов"
    )
    dp.register_message_handler(shutdown_cmd, lambda m: m.text == "⏹ Выключить бота")
    dp.register_message_handler(show_admins, lambda m: m.text == "👑 Показать админов")
    dp.register_message_handler(
        add_admin_start, lambda m: m.text == "👑 Добавить админа", state="*"
    )
    dp.register_message_handler(
        remove_admin_start, lambda m: m.text == "🗑 Удалить админа", state="*"
    )
    dp.register_message_handler(add_chat_button, lambda m: m.text == "➕ Добавить чат")
