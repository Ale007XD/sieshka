"""tests/unit/test_order_service.py"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from nano_vm.models import Program, Step, StepType

from app.domains.orders.models import OrderCreate, OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository
from app.services.order_service import OrderService


@dataclass
class FakeRow:
    _mapping: dict[str, object]


def _make_row(**cols: object) -> FakeRow:
    return FakeRow(_mapping=cols)


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestOrderService:
    @pytest.fixture
    def service(self) -> OrderService:
        return OrderService(session_factory=_session_factory)  # type: ignore[arg-type]

    async def test_create_order(self) -> None:
        customer_id = uuid4()
        order_id = uuid4()
        body = OrderCreate(
            customer_id=customer_id,
            items=[{"sku": "coffee", "qty": 2}],
            delivery_address="Moscow",
        )
        fake_row = _make_row(
            id=order_id,
            customer_id=customer_id,
            state=OrderState.DRAFT.value,
            items=body.items,
            delivery_address="Moscow",
            trace_id=None,
        )
        mock_result = MagicMock()
        mock_result.one.return_value = fake_row
        session = AsyncMock()
        session.execute.return_value = mock_result
        session.commit = AsyncMock()

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.create_order(body)

        assert isinstance(result.id, uuid4().__class__)
        assert result.customer_id == customer_id
        assert result.state == OrderState.DRAFT
        assert result.items == body.items

    async def test_transition_order_success(self) -> None:
        """Uses real VM with real tools and mock session — session DI verified end-to-end."""
        order_id = str(uuid4())
        session = AsyncMock()
        # Tool transition_order_state does: SELECT ... FOR UPDATE; if current == from_state → UPDATE
        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = "DRAFT"
        # Both SELECT and UPDATE hit execute; we only care about the SELECT result
        session.execute.return_value = mock_select
        session.commit = AsyncMock()

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.CONFIRMED
        # commit is called once at service boundary, not inside tool
        session.commit.assert_called_once()

    async def test_transition_order_failure_no_commit_on_tool_error(self) -> None:
        """Multi-step: first tool succeeds, second raises → no commit, zero partial writes."""
        order_id = str(uuid4())
        session = AsyncMock()

        # Step 1: SELECT returns "DRAFT", UPDATE works → tool returns "OK"
        mock_ok = MagicMock()
        mock_ok.scalar_one_or_none.return_value = "DRAFT"
        # Step 2's SELECT → raises exception (DB failure)
        session.execute.side_effect = [
            mock_ok,           # step 1: SELECT ... FOR UPDATE
            MagicMock(),       # step 1: UPDATE (return value unused)
            Exception("DB failure simulated"),  # step 2: SELECT → raises!
        ]
        session.commit = AsyncMock()

        two_step_program = Program(
            name="test_atomicity_raise",
            steps=[
                Step(id="s1", type=StepType.TOOL, tool="transition_order_state",
                     args={"order_id": "$order_id", "from_state": "DRAFT", "to_state": "CONFIRMED"},
                     next_step="s2"),
                Step(id="s2", type=StepType.TOOL, tool="transition_order_state",
                     args={
                         "order_id": "$order_id",
                         "from_state": "CONFIRMED",
                         "to_state": "PAYMENT_PENDING",
                     }),
            ],
        )

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(svc, "_select_program", return_value=two_step_program),
        ):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert isinstance(result, TransitionResult)
        assert result.success is False
        # commit MUST NOT be called — transaction boundary is service-level, not tool-level
        session.commit.assert_not_called()

    async def test_transition_order_rejected(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT):
            result = await svc.transition_order(order_id, OrderEvent.PAYMENT_CONFIRMED)

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.rejected_event == OrderEvent.PAYMENT_CONFIRMED

    async def test_list_orders(self) -> None:
        fake_row = _make_row(
            id=uuid4(),
            customer_id=uuid4(),
            state=OrderState.DRAFT.value,
            items=[],
            delivery_address="Addr",
            trace_id=None,
        )
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [fake_row]
        session = AsyncMock()
        session.execute.return_value = mock_result

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.list_orders()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].state == OrderState.DRAFT

    async def test_list_orders_with_state_filter(self) -> None:
        fake_row = _make_row(
            id=uuid4(),
            customer_id=uuid4(),
            state=OrderState.CONFIRMED.value,
            items=[],
            delivery_address="Addr",
            trace_id=None,
        )
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [fake_row]
        session = AsyncMock()
        session.execute.return_value = mock_result

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.list_orders(state_filter=OrderState.CONFIRMED)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].state == OrderState.CONFIRMED
