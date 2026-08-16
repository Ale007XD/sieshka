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

    NVIDIA_NIM_API_KEY: str = ""
    NVIDIA_NIM_API_BASE: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_NIM_MODEL: str = "nvidia_nim/meta/llama-3.1-70b-instruct"

    # nano-vm MCP (M3+)
    NANO_VM_MCP_HOST: str = "localhost"
    NANO_VM_MCP_PORT: int = 8765

    # YooKassa (M2+)
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = "https://example.com/payment/return"

    # Telegram (M2+; webhook+Mini App fields added sprint_telegram_bot_entrypoint)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    # URL of the Mini App frontend (sprint_telegram_miniapp_frontend, not yet
    # built) — the "Open App" inline keyboard button below needs a real https
    # URL to be usable; empty string degrades to a plain text /start reply,
    # not a broken button (see app/webhooks/telegram.py::telegram_webhook).
    TELEGRAM_MINIAPP_URL: str = ""
    # Outbound proxy for api.telegram.org (2026-08-16): confirmed this VPS's
    # IP (RF-hosted, host1884433-1) cannot reach api.telegram.org at all —
    # IPv6 unreachable, IPv4 connect timeout, verified via curl -v on a bare
    # RF-hosted test box (not a firewall rule on this host — a network-level
    # block upstream of it, same class of issue as ZALO_API_PROXY_URL's
    # doc-check finding, though that one is field-withholding, not a full
    # connect timeout). settings.TELEGRAM_API_PROXY_URL is None-safe: empty
    # string (default) means "call directly, no proxy" — same pattern as
    # ZALO_API_PROXY_URL, config not client logic.
    TELEGRAM_API_PROXY_URL: str = ""

    # MAX messenger (sprint_max_client)
    # platform-api2.max.ru per Mintsifry TLS migration notice (deadline
    # 2026-07-19) — platform-api.max.ru (no "2") is the pre-migration host.
    MAX_BOT_TOKEN: str = ""
    MAX_API_BASE_URL: str = "https://platform-api2.max.ru"
    MAX_WEBHOOK_SECRET: str = ""

    # Zalo Mini App (sprint_zalo_client)
    # ZALO_APP_ID vs MINI_APP_ID: app-level vs mini-app-level identifier,
    # deliberately NOT confused — see ZaloClient module docstring.
    ZALO_APP_ID: str = ""
    ZALO_APP_SECRET_KEY: str = ""
    ZALO_MINI_APP_ID: str = ""
    ZALO_OA_ID: str = ""
    ZALO_API_KEY: str = ""
    # Webhook signature (future sprint_zalo_app_events — app review-status +
    # user consent-revocation events, the only two Mini App Webhook URL
    # event types per official docs, verified 2026-08-13): NOT HMAC, and NOT
    # a separate secret. Per mini.zalo.me/zmp-docs/.../verifysignature —
    # sha256(sorted-keys-content + ZALO_API_KEY) using this same API_KEY
    # above. No separate ZALO_WEBHOOK_SECRET setting — an earlier draft of
    # this config had one based on an unverified HMAC assumption; removed.
    ZALO_API_BASE_URL: str = "https://graph.zalo.me/v2.0"
    ZALO_MINIAPP_API_BASE: str = "https://openapi.mini.zalo.me"
    # Outbound proxy for server-to-server Zalo API calls (get_user_profile,
    # future ZaloPay). None when the host itself has a Vietnam-registered IP
    # — see DECISIONS.md sprint_zalo_client (2026-08-11): Zalo OpenAPI has
    # withheld certain user-data-related response fields from non-VN
    # App/Webhook IPs since 2024-02-29. Empty string ("") is treated as
    # "no proxy", same as None — see ZaloClient.__init__.
    ZALO_API_PROXY_URL: str = ""

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