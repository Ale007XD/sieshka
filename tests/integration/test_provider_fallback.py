"""Integration tests for provider fallback FSM — three scenarios.

1. OpenRouter succeeds — no switch, Trace shows single attempt
2. OpenRouter times out → YandexGPT succeeds — Trace shows exactly one switch_event
3. OpenRouter times out → YandexGPT times out → GigaChat succeeds — Trace shows two switch_events
4. OpenRouter non-timeout failure — does NOT trigger fallback switch
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Generator
from typing import cast
from unittest.mock import patch

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import StepStatus, Trace, TraceStatus
from nano_vm.vm import ExecutionVM
from nano_vm_mcp.store import ProgramStore

from app.db_nano import StoreCursorRepository
from app.programs.llm_fallback_program import PROVIDER_FALLBACK
from app.tools.llm_fallback_tools import (
    attempt_gigachat,
    attempt_openrouter,
    attempt_yandexgpt,
    finalize_success,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def nano_vm() -> Generator[ExecutionVM, None, None]:
    """Build an ExecutionVM with fallback tools registered (no session, no PG)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        store = ProgramStore(path)
        cursor = StoreCursorRepository(store)
        vm = ExecutionVM(llm=cast(MockLLMAdapter, None), cursor_repository=cursor)
        for fn in (attempt_openrouter, attempt_yandexgpt, attempt_gigachat, finalize_success):
            vm.register_tool(fn.__name__, fn)
        yield vm
        store.close()
    finally:
        import os
        os.unlink(path)


async def mock_timeout(*args: object, **kwargs: object) -> tuple[str, None]:
    raise asyncio.TimeoutError()


async def mock_success(*args: object, **kwargs: object) -> tuple[str, None]:
    return ("response ok", None)


async def mock_http_error(*args: object, **kwargs: object) -> tuple[str, None]:
    raise RuntimeError("HTTP 500")


class TestProviderFallback:
    """Provider fallback FSM — three scenarios + non-timeout edge case."""

    async def test_openrouter_succeeds_no_switch(self, nano_vm: ExecutionVM) -> None:
        """OpenRouter succeeds — only attempt_openrouter runs, no fallback."""
        program = PROVIDER_FALLBACK
        with patch("app.llm.providers.openrouter_adapter.complete", mock_success):
            trace: Trace = await nano_vm.run(program, context={"prompt": "hello"})
        assert trace.status == TraceStatus.SUCCESS
        step_ids = [s.step_id for s in trace.steps]
        assert step_ids == ["attempt_openrouter", "check_openrouter", "success"]
        assert trace.steps[0].output == 1

    async def test_openrouter_timeout_yandex_succeeds_one_switch(
        self, nano_vm: ExecutionVM,
    ) -> None:
        """OpenRouter times out → YandexGPT succeeds → exactly one switch."""
        program = PROVIDER_FALLBACK
        with (
            patch("app.llm.providers.openrouter_adapter.complete", mock_timeout),
            patch("app.llm.providers.yandexgpt_adapter.complete", mock_success),
        ):
            trace: Trace = await nano_vm.run(program, context={"prompt": "hello"})
        assert trace.status == TraceStatus.SUCCESS
        step_ids = [s.step_id for s in trace.steps]
        assert step_ids == [
            "attempt_openrouter",
            "check_openrouter",
            "attempt_yandexgpt",
            "check_yandexgpt",
            "success",
        ]
        assert trace.steps[0].output == 0
        assert trace.steps[2].output == 1

    async def test_two_hop_switch_to_gigachat(self, nano_vm: ExecutionVM) -> None:
        """Both OpenRouter and YandexGPT time out → GigaChat succeeds → two switches."""
        program = PROVIDER_FALLBACK
        with (
            patch("app.llm.providers.openrouter_adapter.complete", mock_timeout),
            patch("app.llm.providers.yandexgpt_adapter.complete", mock_timeout),
            patch("app.llm.providers.gigachat_adapter.complete", mock_success),
        ):
            trace: Trace = await nano_vm.run(program, context={"prompt": "hello"})
        assert trace.status == TraceStatus.SUCCESS
        step_ids = [s.step_id for s in trace.steps]
        assert step_ids == [
            "attempt_openrouter",
            "check_openrouter",
            "attempt_yandexgpt",
            "check_yandexgpt",
            "attempt_gigachat",
            "success",
        ]
        assert trace.steps[0].output == 0
        assert trace.steps[2].output == 0
        assert trace.steps[4].output == 1

    async def test_non_timeout_failure_does_not_trigger_fallback(
        self, nano_vm: ExecutionVM,
    ) -> None:
        """Non-timeout error (e.g. HTTP 500) does NOT route through fallback."""
        program = PROVIDER_FALLBACK
        with patch("app.llm.providers.openrouter_adapter.complete", mock_http_error):
            trace: Trace = await nano_vm.run(program, context={"prompt": "hello"})
        assert trace.status == TraceStatus.FAILED
        step_ids = [s.step_id for s in trace.steps]
        assert step_ids == ["attempt_openrouter"]
        assert trace.steps[0].status == StepStatus.FAILED


class TestProgramValidation:
    """PROVIDER_FALLBACK must pass ProgramValidator gate."""

    async def test_passes_validator(self) -> None:
        from nano_vm.validator import ProgramValidator

        report = ProgramValidator(PROVIDER_FALLBACK).validate()
        assert report.is_valid(), f"Program validation failed: {report.summary()}"
