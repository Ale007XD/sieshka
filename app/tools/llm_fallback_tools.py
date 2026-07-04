"""Provider fallback attempt tools — numeric sentinel (0/1) per attempt.

Each tool wraps an LLM adapter call with asyncio.wait_for timeout.
Returns 1 on success, 0 on timeout.
Raises ValueError on non-timeout errors (HTTP error, auth error, etc.).
CONDITION steps downstream check $result.output < 1 to route fallback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _LLMAdapterProto(Protocol):
    """Protocol for LLM adapters with a complete() method."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        ...


async def _attempt_provider(
    adapter: _LLMAdapterProto, provider_name: str, prompt: str, timeout_seconds: float = 15.0,
) -> int:
    coro = adapter.complete([{"role": "user", "content": prompt}])
    try:
        await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("%s: timed out after %ss", provider_name, timeout_seconds)
        return 0
    except Exception as exc:
        logger.error("%s: non-timeout failure: %s", provider_name, exc)
        raise ValueError(f"{provider_name} failed: {exc}") from exc
    else:
        logger.info("%s: success", provider_name)
        return 1


async def attempt_openrouter(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import openrouter_adapter

    return await _attempt_provider(openrouter_adapter, "OpenRouter", prompt, timeout_seconds)


async def attempt_yandexgpt(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import yandexgpt_adapter

    return await _attempt_provider(yandexgpt_adapter, "YandexGPT", prompt, timeout_seconds)


async def attempt_gigachat(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import gigachat_adapter

    return await _attempt_provider(gigachat_adapter, "GigaChat", prompt, timeout_seconds)


async def finalize_success(result: int = 0, **kwargs: object) -> str:
    """Terminal tool: records fallback completion."""

    logger.info("finalize_success: result=%d", result)
    return "OK"
