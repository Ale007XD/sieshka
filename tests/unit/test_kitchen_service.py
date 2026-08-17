"""tests/unit/test_kitchen_service.py — KitchenService.transition_ticket
governed-path tests (sprint_kitchen_governance_migration, 2026-08-16).

Mirrors tests/unit/test_order_service.py's shape exactly: real VM, real
tools, mocked AsyncSession. Previously transition_ticket had ZERO unit-level
coverage — the only exercise of its logic was
tests/integration/test_kitchen_flow.py, which requires Docker/Postgres and
does not run in CI's unit job. This file closes that gap for the new
governed implementation.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.domains.kitchen.fsm import KitchenEvent, KitchenState
from app.fsm.core.base import TransitionResult
from app.repositories.kitchen_repo import KitchenRepository
from app.services.kitchen_service import KitchenService


@dataclass
class FakeRow:
    _mapping: dict[str, object]


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestKitchenServiceTransitionTicket:
    async def test_transition_success(self) -> None:
        """Uses real VM with real tools and mock session — session DI
        verified end-to-end, same pattern as
        test_order_service.py::test_transition_order_success."""
        ticket_id = str(uuid4())
        session = AsyncMock()
        # write_kitchen_state_queued: SELECT ... FOR UPDATE -> "NEW", then UPDATE
        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = "NEW"
        session.execute.return_value = mock_select
        session.commit = AsyncMock()

        svc = KitchenService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(KitchenRepository, "get_state", return_value=KitchenState.NEW):
            result = await svc.transition_ticket(ticket_id, KitchenEvent.QUEUE)

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == KitchenState.QUEUED
        # commit is called once at service boundary, not inside the tool
        session.commit.assert_called_once()

    async def test_transition_rejected_by_graph(self) -> None:
        """Event not allowed from current state — rejected before the
        Program even runs (same as OrderService's graph-level reject)."""
        ticket_id = str(uuid4())
        session = AsyncMock()

        svc = KitchenService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(KitchenRepository, "get_state", return_value=KitchenState.NEW):
            # HAND_OFF is only valid from READY, not NEW
            result = await svc.transition_ticket(ticket_id, KitchenEvent.HAND_OFF)

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.rejected_event == KitchenEvent.HAND_OFF
        session.commit.assert_not_called()

    async def test_transition_tool_failure_no_commit(self) -> None:
        """Ticket not found -> write_kitchen_state_queued raises -> Trace
        FAILED -> no commit. Mirrors test_order_service.py's atomicity test
        intent (no partial state on a governed-write failure)."""
        ticket_id = str(uuid4())
        session = AsyncMock()
        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = None  # ticket not found
        session.execute.return_value = mock_select
        session.commit = AsyncMock()

        svc = KitchenService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(KitchenRepository, "get_state", return_value=KitchenState.NEW):
            result = await svc.transition_ticket(ticket_id, KitchenEvent.QUEUE)

        assert isinstance(result, TransitionResult)
        assert result.success is False
        session.commit.assert_not_called()

    async def test_hand_off_triggers_order_packing(self) -> None:
        """HAND_OFF success -> cross-domain: OrderService.START_PACKING
        called with the ticket's order_id. Unchanged logic from the
        pre-migration implementation, just relocated — this test is new
        (pre-migration had zero unit coverage of it, only integration)."""
        ticket_id = str(uuid4())
        order_id = uuid4()
        session = AsyncMock()

        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = "READY"

        cross_domain_row = MagicMock()
        cross_domain_row.fetchone.return_value = FakeRow(
            _mapping={"order_id": order_id, "delivery_mode": "delivery"}
        )
        session.execute.side_effect = [
            mock_select,  # write_kitchen_state_handed_off: SELECT FOR UPDATE
            MagicMock(),  # write_kitchen_state_handed_off: UPDATE
            cross_domain_row,  # cross-domain SELECT order_id/delivery_mode
        ]
        session.commit = AsyncMock()

        svc = KitchenService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with (
            patch.object(KitchenRepository, "get_state", return_value=KitchenState.READY),
            patch("app.services.order_service.OrderService") as mock_order_service_cls,
        ):
            mock_svc_instance = AsyncMock()
            mock_svc_instance.transition_order.return_value = TransitionResult(
                success=True, new_state=None, rejected_event=None, reason=None
            )
            mock_order_service_cls.return_value = mock_svc_instance

            with patch(
                "app.services.max_staff_notify.notify_courier_order_state",
                new=AsyncMock(),
            ) as mock_notify:
                result = await svc.transition_ticket(ticket_id, KitchenEvent.HAND_OFF)

        assert result.success is True
        mock_svc_instance.transition_order.assert_called_once()
        # delivery_mode="delivery" (not pickup) -> courier notified, order not closed
        mock_notify.assert_called_once()
