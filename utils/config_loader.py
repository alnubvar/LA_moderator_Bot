# utils/config_loader.py
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # подтягиваем переменные из .env

# === Определяем путь до config.json ===
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"

def _load_config_file() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg
    except FileNotFoundError:
        print(f"⚠️ Файл конфигурации не найден: {CONFIG_PATH}")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга config.json: {e}")
        return {}

CONFIG = _load_config_file()

# === Токен ===
BOT_TOKEN = os.getenv("BOT_TOKEN") or CONFIG.get("bot_token")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Укажи его в .env (BOT_TOKEN=...) "
        "или добавь поле 'bot_token' в data/config.json."
    )

# === Остальные настройки ===
ADS_CONTACT = CONFIG.get("ads_contact_username", "@AdmLosAngel")
ADMIN_USERNAMES = {u.lower() for u in CONFIG.get("admin_usernames", [])}
WHITELIST_CFG = {u.lower() for u in CONFIG.get("whitelist", [])}
BAD_PATTERNS_RAW = CONFIG.get("bad_patterns", [])
SANCTIONS = CONFIG.get("sanctions", {})

def load_config():
    """Совместимость со старым кодом"""
    return CONFIG
