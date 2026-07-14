"""Unit tests for FallbackLLMAdapter (debt 3.5).

Verifies the provider hot-switch chain: tries OpenRouter -> YandexGPT ->
GigaChat, surfaces the first successful text, and raises when all fail.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.llm.fallback import FallbackLLMAdapter


def _make_text(response: str):
    async def _fn(*args: object, **kwargs: object) -> tuple[str, None]:
        return (response, None)

    return _fn


async def _timeout(*args: object, **kwargs: object) -> tuple[str, None]:
    raise asyncio.TimeoutError()


async def _err(*args: object, **kwargs: object) -> tuple[str, None]:
    raise RuntimeError("boom")


async def test_returns_first_provider_text() -> None:
    adapter = FallbackLLMAdapter(timeout=2.0)
    with patch("app.llm.providers.openrouter_adapter.complete", _make_text("or")):
        out, _meta = await adapter.complete([{"role": "user", "content": "hi"}])
    assert out == "or"


async def test_falls_through_to_yandex() -> None:
    adapter = FallbackLLMAdapter(timeout=1.0)
    with patch("app.llm.providers.openrouter_adapter.complete", _timeout), patch(
        "app.llm.providers.yandexgpt_adapter.complete", _make_text("yg")
    ):
        out, _meta = await adapter.complete([{"role": "user", "content": "hi"}])
    assert out == "yg"


async def test_falls_through_to_gigachat() -> None:
    adapter = FallbackLLMAdapter(timeout=1.0)
    with patch("app.llm.providers.openrouter_adapter.complete", _timeout), patch(
        "app.llm.providers.yandexgpt_adapter.complete", _timeout
    ), patch("app.llm.providers.gigachat_adapter.complete", _make_text("gc")):
        out, _meta = await adapter.complete([{"role": "user", "content": "hi"}])
    assert out == "gc"


async def test_all_fail_raises() -> None:
    adapter = FallbackLLMAdapter(timeout=1.0)
    with patch("app.llm.providers.openrouter_adapter.complete", _err), patch(
        "app.llm.providers.yandexgpt_adapter.complete", _err
    ), patch("app.llm.providers.gigachat_adapter.complete", _err):
        try:
            await adapter.complete([{"role": "user", "content": "hi"}])
        except RuntimeError:
            return
    raise AssertionError("expected RuntimeError when all providers fail")
