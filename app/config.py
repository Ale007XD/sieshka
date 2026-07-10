"""app/config.py — Settings via pydantic-settings."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://sieshka:sieshka@localhost:5432/sieshka"
    POSTGRES_USER: str = "sieshka"
    POSTGRES_PASSWORD: str = "sieshka"
    SQLITE_PATH: str = "C:/Users/alexd/AppData/Local/Temp/sieshka_nano_vm.db"
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

    # App
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"


settings = Settings()
