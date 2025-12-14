import re
from joblib import load
import logging
from datetime import timedelta, datetime
from typing import Set, Dict
import pytz
from aiogram import types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import BadRequest, MessageToDeleteNotFound
from utils.db import (
    add_or_update_user,
    get_connection,
    get_ad_count,
    increment_ad_count,
    register_chat,
    in_whitelist,
    bump_violation,
)
from utils.config_loader import CONFIG

# === Настройка логов ===
logger = logging.getLogger("LA_Moderator_Bot")

# === Конфиг ===
config = CONFIG
ADS_CONTACT = config.get("ads_contact_username", "@AdmLosAngel")
ADMIN_USERNAMES = set(u.lower() for u in config.get("admin_usernames", []))
WHITELIST_CFG = set(u.lower() for u in config.get("whitelist", []))
BAD_PATTERNS_RAW = config.get("bad_patterns", [])
SANCTIONS = config.get("sanctions", {})

# === ML-конфиг ===
ml_cfg = config.get("ml", {}) if isinstance(config, dict) else {}
ML_ENABLED = bool(ml_cfg.get("enabled", False))
ML_THRESHOLD = float(
    ml_cfg.get("threshold", 0.7)
)  # для совместимости, при LinearSVC это просто флаг

MODEL = None
VECTORIZER = None

if ML_ENABLED:
    try:
        MODEL = load("data/ml/best_model.pkl")
        VECTORIZER = load("data/ml/vectorizer.pkl")
        logger.info("🤖 ML-модель загружена (best_model.pkl).")
    except Exception as e:
        logger.error(f"❌ Не удалось загрузить ML-модель: {e}")
        ML_ENABLED = False

# === Настройки ===
MAX_DAILY_ADS = 4  # дефолтное значение, но теперь оно приходит из БД
BAD_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BAD_PATTERNS_RAW]
_ADMIN_CACHE: Dict[int, Set[int]] = {}

# === Временная зона Лос-Анджелеса ===
LA_TZ = pytz.timezone("America/Los_Angeles")


def la_today_date() -> str:
    """Возвращает сегодняшнюю дату в зоне Лос-Анджелеса (YYYY-MM-DD)."""
    return datetime.now(LA_TZ).date().isoformat()


# ---------- утилиты ----------


def _norm_username(username: str) -> str:
    if not username:
        return ""
    u = username.strip()
    if not u.startswith("@"):
        u = "@" + u
    return u.lower()


async def _is_admin(message: Message) -> bool:
    if message.chat.type not in ("group", "supergroup"):
        return False
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return False
    admins = _ADMIN_CACHE.get(chat_id)
    if admins is None:
        admins = set()
        try:
            admins_list = await message.bot.get_chat_administrators(chat_id)
            for a in admins_list:
                admins.add(a.user.id)
            _ADMIN_CACHE[chat_id] = admins
        except Exception:
            return False
    return user_id in admins


def _in_whitelist(user: types.User) -> bool:
    uname = (user.username or "").strip().lower().lstrip("@")
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM whitelist WHERE LOWER(username)=?", (uname,)
    ).fetchone()
    conn.close()
    return bool(row)


def _extract_text(message: Message) -> str:
    return (message.text or message.caption or "").strip().lower()


def _ads_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            text="Купить рекламу",
            url=f"https://t.me/{ADS_CONTACT.lstrip('@')}",
        )
    )
    return kb


def _mention_html_user(user: types.User) -> str:
    name = (
        (user.full_name or user.username or "пользователь")
        .replace("<", "")
        .replace(">", "")
    )
    return f"<a href='tg://user?id={user.id}'>{name}</a>"


def _is_media(message: Message) -> bool:
    return any(
        [
            message.photo,
            message.video,
            message.animation,
            message.document,
            message.sticker,
            message.voice,
            message.audio,
            message.video_note,
        ]
    )


def _bump_violation_counter(user: types.User) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT violation_count FROM users WHERE id = ?", (user.id,))
    row = cur.fetchone()
    if not row:
        new_val = 1
        cur.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name, last_name, is_violator, violation_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name, 1, new_val),
        )
    else:
        cur_val = row["violation_count"] or 0
        new_val = cur_val + 1
        cur.execute(
            "UPDATE users SET is_violator = 1, violation_count = ? WHERE id = ?",
            (new_val, user.id),
        )
    conn.commit()
    conn.close()
    return new_val


def _mark_last_message_as_ad(user_id: int):
    try:
        conn = get_connection()
        conn.execute(
            """
            UPDATE messages
            SET is_ad = 1
            WHERE id = (
                SELECT id FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (user_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Не удалось пометить сообщение как рекламное: {e}")


# ---------- ML: очистка и предсказание ----------

_URL_RE = re.compile(r"http\S+|www\S+|t\.me/\S+", flags=re.IGNORECASE)
_AT_RE = re.compile(r"@\w+", flags=re.IGNORECASE)
_NONALNUM_RE = re.compile(r"[^а-яa-z0-9\s]", flags=re.IGNORECASE)


def _clean_text_for_ml(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = _URL_RE.sub(" ", text)
    text = _AT_RE.sub(" ", text)
    text = _NONALNUM_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ml_predict_is_ad(raw_text: str):
    """Возвращает (bool, confidence) — реклама или нет и уверенность"""
    if not ML_ENABLED or not MODEL or not VECTORIZER:
        return False, 0.0
    try:
        clean = _clean_text_for_ml(raw_text)
        if not clean:
            return False, 0.0
        X = VECTORIZER.transform([clean])

        # поддержка вероятностей
        if hasattr(MODEL, "predict_proba"):
            probs = MODEL.predict_proba(X)[0]
            confidence = probs[1]
            pred = 1 if confidence >= ML_THRESHOLD else 0
            return bool(pred), float(confidence)
        else:
            pred = MODEL.predict(X)[0]
            return bool(pred), 1.0  # считаем уверенным, если без proba
    except Exception as e:
        logger.warning(f"⚠️ Ошибка ML-предсказания: {e}")
        return False, 0.0


# ---------- ядро модерации ----------


async def check_message(message: Message):
    try:
        # === Регистрируем чат, если его ещё нет в БД ===
        register_chat(message.chat.id, message.chat.title or "")

        # --- работаем только в группах ---
        if message.chat.type not in ("group", "supergroup"):
            return

        # --- если сообщение от канала ---
        if message.sender_chat and not message.from_user:
            text = _extract_text(message)
            is_ad_like = bool(text and any(p.search(text) for p in BAD_PATTERNS))
            if _is_media(message) or is_ad_like:
                try:
                    await message.delete()
                    logger.info(
                        f"🗑 Удалён пост от канала '{message.sender_chat.title}'"
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось удалить пост канала: {e}")
                try:
                    await message.bot.ban_chat_sender_chat(
                        chat_id=message.chat.id, sender_chat_id=message.sender_chat.id
                    )
                    await message.answer(
                        f"🚫 Канал <b>{message.sender_chat.title}</b> заблокирован за рекламу/медиа.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось заблокировать канал: {e}")
            return

        user = message.from_user

        # --- не трогаем админов чата ---
        if await _is_admin(message):
            return

        # --- текст сообщения ---
        text = _extract_text(message)
        if not text:
            return

        # === объединённая детекция рекламы ===
        # 1) по паттернам
        is_ad_by_pattern = any(pattern.search(text) for pattern in BAD_PATTERNS)
        # 2) ML (только если паттерн не сработал)
        is_ad_by_ml, ml_conf = False, 0.0
        logger.info(
            f"[CHAT {message.chat.id}] detect: pattern={is_ad_by_pattern} ml={is_ad_by_ml} conf={ml_conf:.2f} text={text[:80]!r}"
        )

        if ML_ENABLED and not is_ad_by_pattern:
            is_ad_by_ml, ml_conf = ml_predict_is_ad(text)

        is_ad = is_ad_by_pattern or is_ad_by_ml
        is_media_msg = _is_media(message)

        # ✅ --- логика для рекламодателей (whitelist) ---
        if in_whitelist(message.chat.id, user.username):
            ad_data = get_ad_count(message.chat.id, user.id)
            if ad_data and isinstance(ad_data, (list, tuple)) and len(ad_data) == 2:
                ad_count, daily_limit = ad_data
            else:
                ad_count, daily_limit = 0, 4

            ad_count = int(ad_count or 0)
            daily_limit = int(daily_limit or 4)

            if ad_count < daily_limit:
                increment_ad_count(message.chat.id, user.id)
                logger.info(
                    f"📢 Реклама от @{user.username or user.id} "
                    f"({ad_count + 1}/{daily_limit}) — разрешена (whitelist)."
                )
                return

            elif ad_count == daily_limit:
                try:
                    await message.delete()
                except Exception:
                    pass

                await message.answer(
                    f"⚠️ @{user.username or user.full_name}, вы уже опубликовали "
                    f"{daily_limit} рекламных сообщений сегодня.\n"
                    f"Следующие публикации будут удаляться автоматически "
                    f"до начала следующего дня ⏰",
                    parse_mode="HTML",
                )
                logger.warning(
                    f"⚠️ @{user.username or user.id} превысил лимит рекламы "
                    f"({daily_limit}/день). Предупреждён."
                )
                return

            else:
                try:
                    await message.delete()
                except Exception:
                    pass

                # конец текущего LA-дня
                now_la = datetime.now(LA_TZ)
                tomorrow_la = (now_la + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                until_date = int(tomorrow_la.timestamp())

                try:
                    await message.bot.restrict_chat_member(
                        chat_id=message.chat.id,
                        user_id=user.id,
                        permissions=types.ChatPermissions(can_send_messages=False),
                        until_date=until_date,
                    )
                    await message.answer(
                        f"🚫 @{user.username or user.full_name}, лимит рекламы "
                        f"({daily_limit}/сутки) превышен.\n"
                        f"Вы ограничены до начала следующего дня (по времени Лос-Анджелеса).",
                        parse_mode="HTML",
                    )
                    logger.warning(
                        f"🚫 @{user.username or user.id} ограничен до следующего LA-дня "
                        f"(превышен лимит рекламы)."
                    )
                except Exception as e:
                    logger.error(f"❌ Ошибка при ограничении рекламодателя: {e}")
                return

        # === дальше обычная логика наказаний ===

        if is_media_msg:
            try:
                await message.delete()
                logger.info(
                    f"[CHAT {message.chat.id}] 🗑 Удалено медиа от {user.username or user.id}"
                )
            except Exception as e:
                logger.warning(f"⚠️ Ошибка удаления медиа: {e}")
            violation_count = bump_violation(
                message.chat.id, user.id, user.username or ""
            )
            mention = _mention_html_user(user)
            await _apply_sanction(
                message, user.id, violation_count, mention, is_media=True
            )
            return

        if is_ad:
            # — Мягкий режим ML —
            ML_ONLY_WARN = bool(ml_cfg.get("only_warn", False))
            if is_ad_by_ml and ML_ONLY_WARN and ml_conf < ML_THRESHOLD:
                # если сообщение поймано ML и уверенность ниже порога — просто предупредим
                try:
                    await message.reply(
                        f"🤖 Похоже, это реклама (уверенность {ml_conf:.2f}). Проверьте правила группы.",
                        parse_mode="HTML",
                    )
                    logger.info(
                        f"⚠️ Мягкое предупреждение (ML={ml_conf:.2f}) от {user.username or user.id}: {text[:80]!r}"
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при мягком предупреждении: {e}")
                return

            # — Если реклама подтверждена (по паттерну или уверенно ML) —
            try:
                await message.delete()
                logger.info(
                    f"🗑 Удалён рекламный текст от {user.username or user.id} "
                    f"({'PATTERN' if is_ad_by_pattern else 'ML'})"
                )
            except Exception as e:
                logger.warning(f"⚠️ Ошибка удаления: {e}")

            violation_count = bump_violation(
                message.chat.id, user.id, user.username or ""
            )
            _mark_last_message_as_ad(user.id)

            mention = _mention_html_user(user)

            warn = (
                f"⛔️ {mention}, реклама в этой группе <b>платная</b>.\n\n"
                f"По вопросам рекламы — {ADS_CONTACT} ✅"
            )
            try:
                await message.answer(
                    warn, reply_markup=_ads_keyboard(), parse_mode="HTML"
                )
            except Exception:
                pass

            await _apply_sanction(
                message, user.id, violation_count, mention, is_media=False
            )
            return

    except Exception as e:
        logger.exception(f"💥 Критическая ошибка в check_message: {e}")


async def _apply_sanction(
    message: Message,
    user_id: int,
    violation_count: int,
    mention: str,
    is_media: bool = False,
):
    """Применяет наказания в зависимости от количества нарушений."""
    try:
        if violation_count == 1:
            hours = SANCTIONS.get("first_violation_mute_hours", 24)
            until = message.date + timedelta(hours=hours)
            await message.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                permissions=types.ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await message.answer(
                f"⚠️ {mention}, это ваше <b>первое нарушение</b>.\nОграничение на <b>{hours} часов</b> ⏰",
                parse_mode="HTML",
            )
        elif violation_count == 2:
            days = SANCTIONS.get("repeat_violation_mute_days", 30)
            until = message.date + timedelta(days=days)
            await message.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                permissions=types.ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await message.answer(
                f"📆 {mention}, это уже <b>второе нарушение</b>.\nОграничение на <b>{days} дней</b> 🚫",
                parse_mode="HTML",
            )
        else:
            await message.bot.kick_chat_member(chat_id=message.chat.id, user_id=user_id)
            await message.answer(
                f"🚫 {mention}, вы <b>забанены навсегда</b> за систематические нарушения.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning(
            f"⚠️ Ошибка применения санкции ({'media' if is_media else 'text'}): {e}"
        )


async def handle_message(message: Message):
    if message.from_user:
        add_or_update_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name,
            violator=False,
        )
    text = message.text or message.caption
    if text and message.from_user:
        conn = get_connection()
        conn.execute(
            "INSERT INTO messages (chat_id, user_id, username, text) VALUES (?, ?, ?, ?)",
            (message.chat.id, message.from_user.id, message.from_user.username, text),
        )
        conn.commit()
        conn.close()

    await check_message(message)


def register_handlers(dp):
    logger.info("🔌 Регистрация message handlers")
    dp.register_message_handler(handle_message, content_types=types.ContentTypes.TEXT)

    dp.register_message_handler(
        handle_message,
        content_types=[
            types.ContentTypes.PHOTO,
            types.ContentTypes.VIDEO,
            types.ContentTypes.DOCUMENT,
            types.ContentTypes.ANIMATION,
            types.ContentTypes.AUDIO,
            types.ContentTypes.VOICE,
            types.ContentTypes.VIDEO_NOTE,
            types.ContentTypes.STICKER,
        ],
    )


def register_message_handlers(dp):
    register_handlers(dp)
