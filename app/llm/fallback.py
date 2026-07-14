"""app/llm/fallback.py — FallbackLLMAdapter: provider hot-switch chain.

Mirrors the PROVIDER_FALLBACK nano-vm Program: tries OpenRouter -> YandexGPT
-> GigaChat, each wrapped in an asyncio timeout, surfacing the first
successful text. Used as the LLM adapter for the agent programs so they
inherit the same provider resilience (debt 3.5) without changing the agent
programs themselves (their StepType.LLM step stays intact).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class FallbackLLMAdapter:
    """LLM adapter that attempts providers in order until one succeeds.

    Adapters are resolved lazily per call (same pattern as the attempt_* tools)
    so tests can monkeypatch ``app.llm.providers.*_adapter.complete``.
    """

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def complete(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None]:
        from app.llm.providers import (
            gigachat_adapter,
            openrouter_adapter,
            yandexgpt_adapter,
        )

        providers = (
            ("OpenRouter", openrouter_adapter),
            ("YandexGPT", yandexgpt_adapter),
            ("GigaChat", gigachat_adapter),
        )
        last_error: Exception | None = None
        for name, adapter in providers:
            try:
                result = await asyncio.wait_for(
                    adapter.complete(messages, **kwargs), self._timeout
                )
                logger.info("FallbackLLMAdapter: %s succeeded", name)
                if isinstance(result, tuple):
                    return result
                return (str(result), None)
            except asyncio.TimeoutError:
                logger.warning("FallbackLLMAdapter: %s timed out", name)
            except Exception as exc:  # noqa: BLE001 - try next provider
                logger.error("FallbackLLMAdapter: %s failed: %s", name, exc)
                last_error = exc
        raise RuntimeError(
            f"All LLM providers failed; last error: {last_error}"
        ) from last_error
