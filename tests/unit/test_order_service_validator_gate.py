"""ProgramValidator gates every ExecutionVM.run() call site."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from nano_vm.models import Program, Step, StepType, Trace, TraceStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.orders.models import OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository
from app.services.order_service import OrderService

_INVALID_PROGRAM = Program(
    name="invalid",
    steps=[
        Step(
            id="s1",
            type=StepType.TOOL,
            tool="transition_order_state",
            args={},
            next_step="ghost",
            is_terminal=False,
        ),
    ],
)

_VALID_PROGRAM = Program(
    name="valid",
    steps=[
        Step(id="s1", type=StepType.TOOL, tool="transition_order_state", is_terminal=True),
    ],
)


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestProgramValidatorGate:
    """ProgramValidator gates every ExecutionVM.run() call site in OrderService."""

    async def _service(self, mock_vm: AsyncMock | None = None) -> OrderService:
        svc = OrderService(
            session_factory=cast("async_sessionmaker[AsyncSession]", _session_factory),
            vm=mock_vm,
        )
        return svc

    # -- transition_order --

    async def test_transition_order_invalid_program_raises(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_vm = AsyncMock()
        svc = await self._service(mock_vm)
        svc._session_factory = cast(
            "async_sessionmaker[AsyncSession]", lambda: _session_factory(session),
        )

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(svc, "_select_program", return_value=_INVALID_PROGRAM),
        ):
            with pytest.raises(RuntimeError, match="Program 'invalid' validation failed"):
                await svc.transition_order(order_id, OrderEvent.CONFIRM)

        mock_vm.run.assert_not_awaited()

    async def test_transition_order_valid_program_passes(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_vm = AsyncMock()
        mock_vm.run.return_value = Trace(program_name="valid", status=TraceStatus.SUCCESS)
        svc = await self._service(mock_vm)
        svc._session_factory = cast(
            "async_sessionmaker[AsyncSession]", lambda: _session_factory(session),
        )

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(svc, "_select_program", return_value=_VALID_PROGRAM),
        ):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert isinstance(result, TransitionResult)
        assert result.success is True
        mock_vm.run.assert_awaited_once()

    # -- handle_event --

    async def test_handle_event_invalid_program_raises(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_vm = AsyncMock()
        svc = await self._service(mock_vm)
        svc._session_factory = cast(
            "async_sessionmaker[AsyncSession]", lambda: _session_factory(session),
        )

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(svc, "_select_program", return_value=_INVALID_PROGRAM),
        ):
            with pytest.raises(RuntimeError, match="Program 'invalid' validation failed"):
                await svc.handle_event(order_id, OrderEvent.CONFIRM)

        mock_vm.run.assert_not_awaited()

    async def test_handle_event_valid_program_passes(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_vm = AsyncMock()
        mock_vm.run.return_value = Trace(program_name="valid", status=TraceStatus.SUCCESS)
        svc = await self._service(mock_vm)
        svc._session_factory = cast(
            "async_sessionmaker[AsyncSession]", lambda: _session_factory(session),
        )

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(svc, "_select_program", return_value=_VALID_PROGRAM),
        ):
            result = await svc.handle_event(order_id, OrderEvent.CONFIRM)

        assert isinstance(result, TransitionResult)
        assert result.success is True
        mock_vm.run.assert_awaited_once()
