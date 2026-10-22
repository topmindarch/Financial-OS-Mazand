import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_ids: frozenset[int]
    log_level: str

def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_TELEGRAM_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN in .env before starting the bot.")
    raw_ids = os.getenv("ADMIN_TELEGRAM_IDS", "")
    try:
        admin_ids = frozenset(int(x.strip()) for x in raw_ids.split(",") if x.strip())
    except ValueError as exc:
        raise RuntimeError("ADMIN_TELEGRAM_IDS must be comma-separated numeric Telegram IDs.") from exc
    if not admin_ids:
        raise RuntimeError("Set at least one numeric Telegram ID in ADMIN_TELEGRAM_IDS.")
    return Settings(token, os.getenv("DATABASE_URL", "").strip(), admin_ids, os.getenv("LOG_LEVEL", "INFO").upper())
