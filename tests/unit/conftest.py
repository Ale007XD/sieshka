import os

# Set LLM provider env vars BEFORE any test module imports app.config.
# pydantic-settings reads .env at Settings() creation (module import time),
# so env vars must be in place before the first import of app.config.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("YANDEX_API_KEY", "test-yandex-key")
os.environ.setdefault("YANDEX_API_BASE", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
os.environ.setdefault("YANDEX_MODEL", "openai/yandexgpt-pro")
os.environ.setdefault("GIGACHAT_API_KEY", "test-gigachat-key")
os.environ.setdefault("GIGACHAT_API_BASE", "https://gigachat.devices.sberbank.ru/api/v1")
os.environ.setdefault("GIGACHAT_MODEL", "openai/GigaChat-Pro")
