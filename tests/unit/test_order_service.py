"""tests/unit/test_order_service.py"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from nano_vm.models import Trace, TraceStatus

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
        # override to use our specific session
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.create_order(body)

        assert isinstance(result.id, uuid4().__class__)
        assert result.customer_id == customer_id
        assert result.state == OrderState.DRAFT
        assert result.items == body.items

    async def test_transition_order_success(self) -> None:
        order_id = str(uuid4())
        session = AsyncMock()

        mock_vm = AsyncMock()
        mock_vm.run.return_value = Trace(program_name="order_confirm", status=TraceStatus.SUCCESS)

        svc = OrderService(session_factory=_session_factory, vm=mock_vm)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with (
            patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT),
            patch.object(OrderRepository, "write_state"),
        ):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.CONFIRMED
        mock_vm.run.assert_awaited_once()

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
