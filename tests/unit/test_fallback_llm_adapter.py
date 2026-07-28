"""Unit tests for FallbackLLMAdapter (debt 3.5).

Verifies the provider hot-switch chain: tries GigaChat -> NvidiaNIM ->
surfaces the first successful text, and raises when all fail.

Yandex is deliberately NOT part of this chain (2026-07-27 decision — not
configured/used for admin-agent LLM calls). It remains a real hop in the
separate Program-based PROVIDER_FALLBACK FSM (app/programs/
llm_fallback_program.py) — that is a different mechanism, not tested here.
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
    """GigaChat is tried first — succeeds immediately, NvidiaNIM never called."""
    adapter = FallbackLLMAdapter(timeout=2.0)
    with patch("app.llm.providers.gigachat_adapter.complete", _make_text("gc")):
        out, _meta = await adapter.complete([{"role": "user", "content": "hi"}])
    assert out == "gc"


async def test_falls_through_to_nvidia_nim() -> None:
    """GigaChat fails/times out -> NvidiaNIM (the only other hop) succeeds."""
    adapter = FallbackLLMAdapter(timeout=1.0)
    with patch("app.llm.providers.gigachat_adapter.complete", _timeout), patch(
        "app.llm.providers.nvidia_nim_adapter.complete", _make_text("nim")
    ):
        out, _meta = await adapter.complete([{"role": "user", "content": "hi"}])
    assert out == "nim"


async def test_all_fail_raises() -> None:
    adapter = FallbackLLMAdapter(timeout=1.0)
    with patch("app.llm.providers.gigachat_adapter.complete", _err), patch(
        "app.llm.providers.nvidia_nim_adapter.complete", _err
    ):
        try:
            await adapter.complete([{"role": "user", "content": "hi"}])
        except RuntimeError:
            return
    raise AssertionError("expected RuntimeError when all providers fail")