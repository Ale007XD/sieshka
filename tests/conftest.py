"""tests/conftest.py — env vars for all tests (must be first conftest loaded)."""
from __future__ import annotations

import os


def _ensure(var: str, val: str) -> None:
    os.environ[var] = os.environ.get(var) or val


# Set LLM + dashboard env vars BEFORE any test module imports app.config.
# pydantic-settings reads .env at Settings() creation (module import time),
# so this root conftest must run before any test module is imported.
_ensure("OPENAI_API_KEY", "test-openai-key")
_ensure("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
_ensure("NVIDIA_NIM_API_KEY", "test-nvidia-key")
_ensure("YANDEX_API_KEY", "test-yandex-key")
_ensure("YANDEX_API_BASE", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
_ensure("YANDEX_MODEL", "openai/yandexgpt-pro")
_ensure("GIGACHAT_API_KEY", "test-gigachat-key")
_ensure("GIGACHAT_API_BASE", "https://gigachat.devices.sberbank.ru/api/v1")
_ensure("GIGACHAT_MODEL", "openai/GigaChat-Pro")
_ensure("DASHBOARD_USER", "admin")
_ensure(
    "DASHBOARD_PASSWORD_HASH",
    "$argon2id$v=19$m=65536,t=3,p=4$Rag1BoDwfq91Tun93xvDmA$0935uJ7gWHOViyio4hNYpVojNYXNVkGj41ROiYLvvwc",
)
