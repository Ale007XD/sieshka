import os


def _ensure(var: str, val: str) -> None:
    os.environ[var] = os.environ.get(var) or val


# Set LLM provider env vars BEFORE any test module imports app.config.
# pydantic-settings reads .env at Settings() creation (module import time),
# so env vars must be in place before the first import of app.config.
_ensure("OPENAI_API_KEY", "test-openai-key")
_ensure("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
_ensure("YANDEX_API_KEY", "test-yandex-key")
_ensure("YANDEX_API_BASE", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
_ensure("YANDEX_MODEL", "openai/yandexgpt-pro")
_ensure("GIGACHAT_API_KEY", "test-gigachat-key")
_ensure("GIGACHAT_API_BASE", "https://gigachat.devices.sberbank.ru/api/v1")
_ensure("GIGACHAT_MODEL", "openai/GigaChat-Pro")
