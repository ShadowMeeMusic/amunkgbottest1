import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота (обязательно в .env)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле! Укажи его: BOT_TOKEN=твой_токен")

# ID главных админов (через запятую в .env, например: 123456789,987654321)
CHIEF_ADMIN_IDS_STR = os.getenv("CHIEF_ADMIN_IDS", "")
if not CHIEF_ADMIN_IDS_STR.strip():
    raise ValueError("CHIEF_ADMIN_IDS не найден в .env! Укажи хотя бы свой ID")

CHIEF_ADMIN_IDS = [int(id_str.strip()) for id_str in CHIEF_ADMIN_IDS_STR.split(",") if id_str.strip()]

# Путь к базе данных SQLite
DB_PATH = "mun_bot.db"

TECH_SPECIALIST_ID = int(os.getenv("TECH_SPECIALIST_ID"))
if not TECH_SPECIALIST_ID:
    raise ValueError("TECH_SPECIALIST_ID не в .env!")


