from __future__ import annotations

from nano_vm.adapters.litellm_adapter import LiteLLMAdapter

from app.config import settings

# Primary: OpenRouter free tier
# Requires OPENAI_API_KEY + OPENAI_API_BASE env vars set BEFORE litellm import.
# api_base must be https://openrouter.ai/api/v1
openrouter_adapter = LiteLLMAdapter(
    model="openrouter/meta-llama/llama-3.1-8b-instruct:free",
    timeout=30.0,
    max_retries=2,
    temperature=0.0,
    stream=True,
    max_tokens=8192,
)

# Fallback 1: YandexGPT Pro
yandexgpt_adapter = LiteLLMAdapter(
    model=settings.YANDEX_MODEL,
    timeout=30.0,
    max_retries=2,
    temperature=0.0,
    api_key=settings.YANDEX_API_KEY,
    api_base=settings.YANDEX_API_BASE,
    stream=True,
    max_tokens=8192,
)

# Fallback 2: GigaChat
gigachat_adapter = LiteLLMAdapter(
    model=settings.GIGACHAT_MODEL,
    timeout=30.0,
    max_retries=2,
    temperature=0.0,
    api_key=settings.GIGACHAT_API_KEY,
    api_base=settings.GIGACHAT_API_BASE,
    stream=True,
    max_tokens=8192,
)
