"""app/config.py — Settings via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://sieshka:sieshka@localhost:5432/sieshka"
    POSTGRES_USER: str = "sieshka"
    POSTGRES_PASSWORD: str = "sieshka"
    SQLITE_PATH: str = str(_PROJECT_ROOT / "data" / "sieshka_nano_vm.db")
    # nano-vm-mcp SQLite WAL

    # LLM Providers (M4+)
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://openrouter.ai/api/v1"

    YANDEX_API_KEY: str = ""
    YANDEX_API_BASE: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    YANDEX_MODEL: str = "openai/yandexgpt-pro"

    GIGACHAT_API_KEY: str = ""
    GIGACHAT_API_BASE: str = "https://gigachat.devices.sberbank.ru/api/v1"
    GIGACHAT_MODEL: str = "openai/GigaChat-Pro"

    # nano-vm MCP (M3+)
    NANO_VM_MCP_HOST: str = "localhost"
    NANO_VM_MCP_PORT: int = 8765

    # YooKassa (M2+)
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = "https://example.com/payment/return"

    # Telegram (M2+)
    TELEGRAM_BOT_TOKEN: str = ""

    # Dashboard auth (M6+)
    DASHBOARD_USER: str = "admin"
    DASHBOARD_PASSWORD_HASH: str = ""

    # Delivery
    DELIVERY_FEE: int = 99

    # Menu availability window (M7)
    # IANA timezone used to decide the current morning/evening window.
    # Per-project: set MENU_TIMEZONE in .env (e.g. "UTC", "Europe/Moscow",
    # "Asia/Ho_Chi_Minh"). Falls back to UTC if the name is unknown.
    MENU_TIMEZONE: str = "UTC"
    # Hour (local to MENU_TIMEZONE) at which "morning" switches to "evening".
    MENU_MORNING_END_HOUR: int = 16

    # App
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"


settings = Settings()
