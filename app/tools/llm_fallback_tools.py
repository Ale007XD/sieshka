"""Provider fallback attempt tools — numeric sentinel (0/1) per attempt.

Each tool wraps an LLM adapter call with asyncio.wait_for timeout.
Returns 1 on success, 0 on timeout. Raises ValueError on non-timeout errors
(HTTP error, auth error, etc.).
CONDITION steps downstream check $result.output < 1 to route fallback.

The actual LLM text of the successful attempt is captured (debt 3.4) into a
per-run store keyed by trace_id so finalize_success can propagate it instead
of discarding it.
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


_LLM_OUTPUTS: dict[str, str] = {}


def _capture_text(kwargs: dict[str, object], text: str) -> None:
    key = str(kwargs.get("trace_id") or "__fallback__")
    _LLM_OUTPUTS[key] = text


def _get_text(kwargs: dict[str, object]) -> str | None:
    key = str(kwargs.get("trace_id") or "__fallback__")
    return _LLM_OUTPUTS.get(key)


async def _attempt_provider(
    adapter: _LLMAdapterProto, provider_name: str, prompt: str, timeout_seconds: float = 15.0,
) -> str | None:
    coro = adapter.complete([{"role": "user", "content": prompt}])
    try:
        result = await asyncio.wait_for(coro, timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("%s: timed out after %ss", provider_name, timeout_seconds)
        return None
    except Exception as exc:
        logger.error("%s: non-timeout failure: %s", provider_name, exc)
        raise ValueError(f"{provider_name} failed: {exc}") from exc
    else:
        logger.info("%s: success", provider_name)
        if isinstance(result, tuple):
            return result[0]
        return str(result)


async def attempt_openrouter(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import openrouter_adapter

    text = await _attempt_provider(openrouter_adapter, "OpenRouter", prompt, timeout_seconds)
    if text is not None:
        _capture_text(kwargs, text)
    return 1 if text is not None else 0


async def attempt_yandexgpt(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import yandexgpt_adapter

    text = await _attempt_provider(yandexgpt_adapter, "YandexGPT", prompt, timeout_seconds)
    if text is not None:
        _capture_text(kwargs, text)
    return 1 if text is not None else 0


async def attempt_gigachat(prompt: str, timeout_seconds: float = 15.0, **kwargs: object) -> int:
    from app.llm.providers import gigachat_adapter

    text = await _attempt_provider(gigachat_adapter, "GigaChat", prompt, timeout_seconds)
    if text is not None:
        _capture_text(kwargs, text)
    return 1 if text is not None else 0


async def finalize_success(
    result: int = 0, llm_text: str | None = None, **kwargs: object
) -> str:
    """Terminal tool: records fallback completion and propagates the real LLM text.

    The successful provider's text is taken from the explicit llm_text arg when
    provided, otherwise from the per-run capture store (debt 3.4).
    """

    text = llm_text if llm_text is not None else _get_text(kwargs)
    logger.info("finalize_success: result=%d text=%r", result, (text or "")[:200])
    return text or "OK"
