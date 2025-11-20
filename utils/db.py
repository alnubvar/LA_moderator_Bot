# db.py
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from utils.config_loader import BOT_TOKEN
from pathlib import Path
import shutil
import os
import pytz

logger = logging.getLogger("LA_Moderator_Bot")
LA_TZ = pytz.timezone("America/Los_Angeles")

# === Пути ===
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "users.db"
BACKUP_DIR = BASE_DIR / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# === Константы ===
DEFAULT_DAILY_LIMIT = 4  # лимит реклам в сутки


def la_today_date(force_midnight: bool = False) -> str:
    """
    Возвращает текущую дату по Лос-Анджелесу (формат YYYY-MM-DD).
    Если force_midnight=True — всегда округляет до полуночи LA.
    """
    now = datetime.now(LA_TZ)
    if force_midnight:
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.date().isoformat()


# === РЕЗЕРВНЫЕ КОПИИ ===
def backup_database(src: str = None, dst: str = None):
    """Создаёт резервную копию базы и удаляет старые (старше 7 дней)."""
    try:
        source_path = Path(src) if src else DB_PATH
        if not source_path.exists():
            logger.info("ℹ️ База данных ещё не создана — бэкап не требуется.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = Path(dst) if dst else BACKUP_DIR / f"users_backup_{timestamp}.db"

        shutil.copy(source_path, backup_path)
        logger.info(f"💾 Создана резервная копия базы: {backup_path.name}")

        cleanup_old_backups()
    except Exception as e:
        logger.error(f"❌ Ошибка при создании резервной копии: {e}")


def cleanup_old_backups(days_to_keep: int = 7, max_backups: int = 10):
    """Удаляет резервные копии старше N дней или оставляет только последние max_backups."""
    try:
        backups = sorted(BACKUP_DIR.glob("users_backup_*.db"), key=os.path.getmtime)
        cutoff = datetime.now() - timedelta(days=days_to_keep)
        deleted = 0

        for file in backups[:-max_backups]:
            try:
                file_time_str = file.name.replace("users_backup_", "").replace(
                    ".db", ""
                )
                file_time = datetime.strptime(file_time_str, "%Y-%m-%d_%H-%M-%S")
                if file_time < cutoff:
                    os.remove(file)
                    deleted += 1
            except Exception:
                continue

        if deleted:
            logger.info(f"🧹 Удалено старых резервных копий: {deleted}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при очистке бэкапов: {e}")


# === СОЕДИНЕНИЕ С БД ===
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# === УТИЛИТЫ МИГРАЦИЙ ===
def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table});")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    return cur.fetchone() is not None


# === ИНИЦИАЛИЗАЦИЯ / МИГРАЦИИ ===
def init_db():
    """
    Создаёт все нужные таблицы и добавляет недостающие поля.
    Переводит лимиты и whitelist на мультичатную схему.
    """
    backup_database()

    conn = get_connection()
    cur = conn.cursor()

    # USERS (глобальная таблица по user_id)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_violator INTEGER DEFAULT 0,
            violation_count INTEGER DEFAULT 0
        )
        """
    )

    # MESSAGES (добавим chat_id, если нет)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            text TEXT,
            is_ad INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    if not _has_column(cur, "messages", "chat_id"):
        try:
            cur.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER")
        except Exception:
            pass

    # VIOLATIONS (отдельные записи по нарушениям; добавим chat_id)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            reason TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    if not _has_column(cur, "violations", "chat_id"):
        try:
            cur.execute("ALTER TABLE violations ADD COLUMN chat_id INTEGER")
        except Exception:
            pass

    # ADMINS (как было)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # CHATS (реестр чатов)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --- MIGRATION: LIMITS → LIMITS_V2 (chat_id + составной PK) ---
    # Старый формат имел PRIMARY KEY(user_id) и не имел chat_id.
    need_limits_migration = False
    if _table_exists(cur, "limits"):
        cur.execute("PRAGMA table_info(limits);")
        cols = [r[1] for r in cur.fetchall()]
        if "chat_id" not in cols:
            need_limits_migration = True
    else:
        # таблицы limits нет — просто создадим v2
        pass

    if need_limits_migration:
        logger.warning(
            "🔧 Миграция: преобразование таблицы limits → limits_v2 (перс-чатные лимиты)."
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS limits_v2 (
                chat_id INTEGER,
                user_id INTEGER,
                count INTEGER DEFAULT 0,
                last_reset DATE,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        # переноcим старые данные как "глобальные" (chat_id NULL)
        cur.execute(
            "INSERT OR REPLACE INTO limits_v2 (chat_id, user_id, count, last_reset) SELECT NULL, user_id, count, last_reset FROM limits"
        )
        cur.execute("DROP TABLE limits;")
        cur.execute("ALTER TABLE limits_v2 RENAME TO limits;")
        # индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_user ON limits(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_chat ON limits(chat_id)")

    else:
        # если миграция не нужна — удостоверимся, что финальная структура есть
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS limits (
                chat_id INTEGER,
                user_id INTEGER,
                count INTEGER DEFAULT 0,
                last_reset DATE,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_user ON limits(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_chat ON limits(chat_id)")

    # --- MIGRATION: WHITELIST → WHITELIST_V2 (chat_id + уникальность в рамках чата) ---
    if _table_exists(cur, "whitelist"):
        cur.execute("PRAGMA table_info(whitelist);")
        wcols = [r[1] for r in cur.fetchall()]
        if "chat_id" not in wcols:
            logger.warning(
                "🔧 Миграция: преобразование whitelist → whitelist_v2 (перс-чатный)."
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS whitelist_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    username TEXT,
                    until_date DATE,
                    UNIQUE (chat_id, username)
                )
                """
            )
            # старые записи считаем глобальными (chat_id NULL)
            cur.execute(
                "INSERT OR IGNORE INTO whitelist_v2 (chat_id, username, until_date) SELECT NULL, LOWER(username), until_date FROM whitelist"
            )
            cur.execute("DROP TABLE whitelist;")
            cur.execute("ALTER TABLE whitelist_v2 RENAME TO whitelist;")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_user ON whitelist(username)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_chat ON whitelist(chat_id)"
            )
    else:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                username TEXT,
                until_date DATE,
                UNIQUE (chat_id, username)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_whitelist_user ON whitelist(username)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_whitelist_chat ON whitelist(chat_id)"
        )

    conn.commit()
    conn.close()
    logger.info("✅ Все таблицы проверены, структура базы актуальна.")


# === ОСНОВНЫЕ ОПЕРАЦИИ ===
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
            is_violator=is_violator OR excluded.is_violator
        """,
        (user_id, username, first_name, last_name, int(violator)),
    )
    conn.commit()
    conn.close()


def register_chat(chat_id: int, title: str):
    """Регистрирует новый чат в БД (если ещё не добавлен)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title) VALUES (?, ?)",
        (chat_id, title),
    )
    conn.commit()

    # Проверяем, добавился ли новый чат
    if cursor.rowcount > 0:
        logger.info(f"🆕 Новый чат зарегистрирован: {title} ({chat_id})")

        # --- Уведомление админу в Telegram ---
        try:
            bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
            admin_id = 580759300  # твой Telegram ID
            text = (
                f"🆕 <b>Новый чат добавлен:</b>\n"
                f"{title or 'Без названия'}\n"
                f"<code>{chat_id}</code>"
            )
            # создаём фоновую задачу, чтобы не блокировать поток
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bot.send_message(admin_id, text))
            except RuntimeError:
                # если нет активного цикла (например при init_db)
                # отправим синхронно
                asyncio.run(bot.send_message(admin_id, text))

        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить уведомление админу: {e}")

    conn.close()


def get_all_chats():
    conn = get_connection()
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT chat_id, title, added_at FROM chats ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    return rows


# === СООБЩЕНИЯ ===
def add_message(chat_id: int, user_id: int, username: str, text: str, is_ad: int = 0):
    conn = get_connection()
    conn.execute(
        "INSERT INTO messages (chat_id, user_id, username, text, is_ad) VALUES (?, ?, ?, ?, ?)",
        (chat_id, user_id, username, text, int(is_ad)),
    )
    conn.commit()
    conn.close()


# === НАРУШЕНИЯ (перс-чатные) ===
def bump_violation(chat_id: int, user_id: int, username: str, reason: str = "") -> int:
    """Записывает нарушение и возвращает кол-во нарушений пользователя в ЭТОМ чате."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO violations (chat_id, user_id, username, reason) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, username, reason),
    )
    conn.commit()

    cur.execute(
        "SELECT COUNT(*) AS c FROM violations WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    c = cur.fetchone()["c"]

    # Поддержим старое поле users.violation_count для совместимости (глобально, бестолково, но не ломаем)
    cur.execute(
        "UPDATE users SET is_violator = 1, violation_count = ? WHERE id = ?",
        (c, user_id),
    )
    conn.commit()
    conn.close()
    return c


def get_violation_count(chat_id: int, user_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM violations WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ).fetchone()
    conn.close()
    return int(row["c"] if row else 0)


# === ЛИМИТЫ (перс-чатные) ===
def get_ad_count(chat_id: int, user_id: int):
    """
    Возвращает (count, daily_limit) для конкретного (chat_id, user_id).
    Если нет записи — создаёт с нуля для текущего LA-дня.
    Поддерживается fallback: если нет в данном чате, но есть глобальная запись (chat_id NULL) — можно её прочитать, но обновлять будем уже для данного чата.
    """
    conn = get_connection()
    cur = conn.cursor()
    today = la_today_date()

    row = cur.execute(
        "SELECT count, last_reset FROM limits WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ).fetchone()

    if not row:
        # попробуем глобальную запись
        row = cur.execute(
            "SELECT count, last_reset FROM limits WHERE chat_id IS NULL AND user_id = ?",
            (user_id,),
        ).fetchone()

    if not row:
        cur.execute(
            "INSERT OR REPLACE INTO limits (chat_id, user_id, count, last_reset) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, 0, today),
        )
        conn.commit()
        conn.close()
        return 0, DEFAULT_DAILY_LIMIT

    count, last_reset = row["count"], row["last_reset"]
    conn.close()

    if not last_reset or last_reset != today:
        return 0, DEFAULT_DAILY_LIMIT
    return int(count or 0), DEFAULT_DAILY_LIMIT


def increment_ad_count(chat_id: int, user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    today = la_today_date()

    row = cur.execute(
        "SELECT count, last_reset FROM limits WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ).fetchone()

    if not row:
        # создаём новую запись для этого чата
        cur.execute(
            "INSERT INTO limits (chat_id, user_id, count, last_reset) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, 1, today),
        )
    else:
        count, last_reset = row["count"], row["last_reset"]
        if not last_reset or last_reset != today:
            cur.execute(
                "UPDATE limits SET count = 1, last_reset = ? WHERE chat_id = ? AND user_id = ?",
                (today, chat_id, user_id),
            )
        else:
            cur.execute(
                "UPDATE limits SET count = count + 1 WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
    conn.commit()
    conn.close()


def reset_all_limits():
    """
    Ежедневный сброс: не удаляем строки, а обнуляем count и обновляем last_reset на сегодняшний LA-день
    для всех записей, где last_reset != today.
    """
    conn = get_connection()
    today = la_today_date()
    cur = conn.cursor()
    cur.execute(
        "UPDATE limits SET count = 0, last_reset = ? WHERE last_reset IS NULL OR last_reset != ?",
        (today, today),
    )
    conn.commit()
    conn.close()

    logger.info("─" * 60)
    logger.info(f"🌅 НАЧАЛО НОВОГО ДНЯ ПО LA — {today}")
    logger.info("─" * 60)
    logger.info(f"🔁 Суточные лимиты рекламодателей сброшены ({today}, LA-время).")


# === WHITELIST (перс-чатный с глобальным fallback) ===
def in_whitelist(chat_id: int, username: str) -> bool:
    """
    Проверяет whitelist для конкретного чата, с fallback на глобальную запись (chat_id IS NULL).
    """
    if not username:
        return False
    uname = username.strip().lower().lstrip("@")

    conn = get_connection()
    cur = conn.cursor()
    today = datetime.utcnow().date().isoformat()

    # приоритет: запись в этом чате
    row = cur.execute(
        "SELECT 1 FROM whitelist WHERE chat_id = ? AND LOWER(username) = ? AND (until_date IS NULL OR until_date >= ?)",
        (chat_id, uname, today),
    ).fetchone()
    if row:
        conn.close()
        return True

    # затем — глобальная запись
    row = cur.execute(
        "SELECT 1 FROM whitelist WHERE chat_id IS NULL AND LOWER(username) = ? AND (until_date IS NULL OR until_date >= ?)",
        (uname, today),
    ).fetchone()
    conn.close()
    return bool(row)


def add_to_whitelist(
    chat_id: int, username: str, days: int, daily_limit: int = DEFAULT_DAILY_LIMIT
):
    """
    Добавляет пользователя в whitelist КОНКРЕТНОГО чата.
    Сбрасывает нарушения и лимит только в этом чате.
    """
    conn = get_connection()
    cur = conn.cursor()
    until_date = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
    uname = username.lower().lstrip("@")

    # whitelist per chat
    cur.execute(
        """
        INSERT OR REPLACE INTO whitelist (chat_id, username, until_date)
        VALUES (?, ?, ?)
        """,
        (chat_id, uname, until_date),
    )

    # users — как раньше, на всякий (глобальный справочник)
    cur.execute(
        """
        INSERT OR IGNORE INTO users (id, username, first_name, last_name, is_violator, violation_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (None, uname, None, None, 0, 0),
    )

    # снять статус нарушителя глобально (совместимость)
    cur.execute(
        "UPDATE users SET is_violator = 0, violation_count = 0 WHERE LOWER(username)=?",
        (uname,),
    )

    # сброс лимита только для этого чата
    cur.execute(
        """
        INSERT OR REPLACE INTO limits (chat_id, user_id, count, last_reset)
        VALUES (
            ?,
            (SELECT id FROM users WHERE LOWER(username)=? LIMIT 1),
            0,
            ?
        )
        """,
        (chat_id, uname, la_today_date()),
    )

    conn.commit()
    conn.close()
    logger.info(
        f"✅ @{uname} добавлен в whitelist чата {chat_id}: лимит {daily_limit}/день, нарушения сброшены."
    )


def remove_from_whitelist(chat_id: int, username: str):
    uname = (username or "").lower().lstrip("@")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM whitelist WHERE chat_id = ? AND LOWER(username) = ?",
        (chat_id, uname),
    )
    # лимиты только для этого чата
    cur.execute(
        "DELETE FROM limits WHERE chat_id = ? AND user_id IN (SELECT id FROM users WHERE LOWER(username)=?)",
        (chat_id, uname),
    )
    conn.commit()
    conn.close()
    logger.info(f"🗑 @{uname} удалён из whitelist чата {chat_id} и его лимиты очищены.")


def get_whitelist(chat_id: int):
    """
    Возвращает whitelist для данного чата (без глобальных записей).
    Если нужно видеть и глобальные, можно сделать отдельную функцию.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT username, until_date FROM whitelist WHERE chat_id = ? ORDER BY username",
        (chat_id,),
    ).fetchall()
    conn.close()
    return rows


# === ADMINS ===
def add_admin(username, added_by=None):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO admins (username, added_by) VALUES (?, ?)",
        (username.lower(), added_by),
    )
    conn.commit()
    conn.close()
    logger.info(
        f"👑 Добавлен новый админ @{username} (добавлен {added_by or 'системой'})."
    )


def remove_admin(username):
    conn = get_connection()
    conn.execute("DELETE FROM admins WHERE username = ?", (username.lower(),))
    conn.commit()
    conn.close()
    logger.info(f"🧹 Админ @{username} удалён из списка.")


def get_admins():
    conn = get_connection()
    rows = conn.execute("SELECT username FROM admins").fetchall()
    conn.close()
    return [r["username"] for r in rows]


def backup_db():
    """Создаёт резервную копию базы данных в папке data/backups."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUP_DIR / f"users_backup_{timestamp}.db"
        shutil.copy(DB_PATH, backup_file)
        logger.info(f"💾 Бэкап БД создан: {backup_file.name}")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании бэкапа БД: {e}")


def cleanup_old_violations(days: int = 90):
    """Удаляет нарушения старше указанного количества дней (по дате добавления записи)."""
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Проверяем, есть ли колонка "date" или "timestamp"
        cur.execute("PRAGMA table_info(violations);")
        cols = [r[1] for r in cur.fetchall()]
        date_col = None
        for c in ("date", "timestamp", "added_at", "created_at"):
            if c in cols:
                date_col = c
                break

        if not date_col:
            logger.warning(
                "⚠️ В таблице violations нет поля с датой — очистка пропущена."
            )
            conn.close()
            return

        # Удаляем старые записи
        query = f'DELETE FROM violations WHERE "{date_col}" < date("now", ?)'
        cur.execute(query, (f"-{days} day",))
        conn.commit()

        deleted = cur.rowcount
        conn.close()

        if deleted:
            logger.info(f"🧹 Удалено старых нарушений: {deleted} (старше {days} дн.)")
        else:
            logger.info(f"🧹 Очистка нарушений завершена, старых записей не найдено.")
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке нарушений: {e}")


# ====== ОБРАТНАЯ СОВМЕСТИМОСТЬ (старые вызовы без chat_id) ======
# Эти функции держим, чтобы ничего внезапно не отвалилось. Они работают в "глобальном" режиме (chat_id = NULL).


def get_ad_count_global(user_id: int):
    return get_ad_count(None, user_id)


def increment_ad_count_global(user_id: int):
    return increment_ad_count(None, user_id)


def add_to_whitelist_global(
    username: str, days: int, daily_limit: int = DEFAULT_DAILY_LIMIT
):
    return add_to_whitelist(None, username, days, daily_limit)


def in_whitelist_global(username: str) -> bool:
    return in_whitelist(None, username)


def remove_from_whitelist_global(username: str):
    return remove_from_whitelist(None, username)
