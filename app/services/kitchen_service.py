from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from nano_vm.models import Program, Trace, TraceStatus
from nano_vm.validator import ProgramValidator
from nano_vm_mcp.handlers import GovernedToolExecutor
from opentelemetry import trace as otel_trace
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.kitchen.fsm import KITCHEN_TRANSITIONS, KitchenEvent, KitchenState
from app.fsm.core.base import TransitionResult
from app.policy.policy_snapshot import KITCHEN_POLICY_SNAPSHOT
from app.programs.kitchen_programs import EVENT_PROGRAM_MAP
from app.repositories.kitchen_repo import KitchenRepository
from app.tools.kitchen_tools import (
    write_kitchen_state_handed_off,
    write_kitchen_state_preparing,
    write_kitchen_state_queued,
    write_kitchen_state_ready,
)

logger = logging.getLogger(__name__)
_tracer = otel_trace.get_tracer("sieshka")


class _VMProtocol(Protocol):
    """Minimal protocol for ExecutionVM duck-typing — same shape as
    OrderService's (app/services/order_service.py)."""

    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class KitchenTicketRead(BaseModel):
    id: UUID
    order_id: UUID
    state: KitchenState


class KitchenService:
    """Composition root: wires KitchenRepository -> KitchenFSM."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        vm: _VMProtocol | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vm = vm

    def _transition_vm(self, session: AsyncSession) -> _VMProtocol:
        """Return a VM per-transition, bound to the given session.

        If a test VM was injected via constructor, return it as-is — same
        DI shape as OrderService._transition_vm.
        """
        if self._vm is not None:
            return self._vm
        return _build_vm(session)

    async def create_ticket(self, order_id: str) -> KitchenTicketRead:
        async with self._session_factory() as session:
            repo = KitchenRepository(session)
            ticket_id = await repo.create(order_id)
            await session.commit()
            return KitchenTicketRead(
                id=UUID(ticket_id),
                order_id=UUID(order_id),
                state=KitchenState.NEW,
            )

    async def transition_ticket(
        self,
        ticket_id: str,
        event: KitchenEvent,
    ) -> TransitionResult:
        """Governed transition (sprint_kitchen_governance_migration,
        2026-08-16): routes through ExecutionVM/GovernedToolExecutor via
        app.programs.kitchen_programs, same shape as
        OrderService.transition_order. Replaces the old direct
        KitchenFSM + KitchenRepository.write_state path — both retained
        in place (not deleted) for rollback safety, same convention as
        OrderFSM (see app/domains/orders/fsm.py + OrderService's own
        docstring: "OrderFSM retained (deprecated) ... for rollback
        safety").
        """
        async with self._session_factory() as session:
            repo = KitchenRepository(session)
            current_state = await repo.get_state(ticket_id)

            allowed = KITCHEN_TRANSITIONS.get(current_state, {})
            if event not in allowed:
                logger.warning(
                    "KitchenService: rejected ticket=%s event=%s from state=%s",
                    ticket_id, event, current_state,
                )
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=event,
                    reason=f"Event {event!r} not allowed from state {current_state!r}",
                )

            new_state = allowed[event]
            program = EVENT_PROGRAM_MAP[event.value]
            context = {"ticket_id": ticket_id}

            _report = ProgramValidator(program).validate()
            if not _report.is_valid():
                raise RuntimeError(
                    f"Program '{program.name}' validation failed: {_report.summary()}"
                )

            with _tracer.start_as_current_span(
                "sieshka.kitchen_transition",
                attributes={
                    "ticket_id": ticket_id,
                    "event_type": event.value,
                    "program_name": program.name,
                },
            ):
                trace = await self._transition_vm(session).run(program, context=context)

            # Persist trace to SQLite store so receipt viewer works — same
            # shape as OrderService.transition_order.
            if trace.trace_id:
                from app.db_nano import get_store

                get_store().save_trace(
                    trace_id=trace.trace_id,
                    program_id=trace.program_name,
                    status=trace.status.value,
                    steps_count=len(trace.steps),
                    total_cost=trace.total_cost_usd() or 0.0,
                    trace=trace.model_dump(mode="json"),
                )

            if trace.status != TraceStatus.SUCCESS:
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=event,
                    reason=trace.error or "Execution failed",
                )

            result = TransitionResult(
                success=True,
                new_state=new_state,
                rejected_event=None,
                reason=None,
            )
            await session.commit()

        # Cross-domain: HANDED_OFF → advance order to PACKING, then
        # auto-close pickup orders (delivery_mode='pickup'). Unchanged from
        # the pre-migration implementation — runs after commit, outside the
        # governed transition's own session/transaction, same as before.
        if event == KitchenEvent.HAND_OFF and result.success:
            try:
                from app.domains.orders.models import OrderEvent
                from app.services.order_service import OrderService

                async with self._session_factory() as session:
                    row = await session.execute(
                        text(
                            "SELECT kt.order_id, o.delivery_mode "
                            "FROM kitchen_tickets kt "
                            "JOIN orders o ON o.id = kt.order_id "
                            "WHERE kt.id = :id"
                        ),
                        {"id": UUID(ticket_id)},
                    )
                    record = row.fetchone()

                if record is not None:
                    order_id = str(record._mapping["order_id"])
                    delivery_mode = record._mapping["delivery_mode"]
                    svc = OrderService(session_factory=self._session_factory)

                    packing = await svc.transition_order(order_id, OrderEvent.START_PACKING)
                    if not packing.success:
                        logger.warning(
                            "KitchenService: START_PACKING failed for order %s: %s",
                            order_id, packing.reason,
                        )
                    elif delivery_mode == "pickup":
                        close = await svc.transition_order(order_id, OrderEvent.CLOSE)
                        if not close.success:
                            logger.warning(
                                "KitchenService: CLOSE failed for pickup order %s: %s",
                                order_id, close.reason,
                            )
                    else:
                        # sprint_max_staff_notify: pickup orders skip courier
                        # entirely (closed above); delivery orders now need a
                        # courier — broadcast PACKING/ASSIGN_COURIER to every
                        # active courier. Fire-and-forget by construction
                        # (notify_courier_order_state never raises) — this
                        # runs after packing.success already returned True,
                        # so there is no transition left here for a
                        # notification failure to roll back.
                        from app.domains.orders.models import OrderState
                        from app.services.max_staff_notify import (
                            notify_courier_order_state,
                        )

                        await notify_courier_order_state(order_id, OrderState.PACKING)
            except Exception:
                logger.exception(
                    "KitchenService: error advancing order after HAND_OFF ticket=%s",
                    ticket_id,
                )

        return result

    async def get_order_id(self, ticket_id: str) -> str | None:
        """sprint_max_staff_notify: resolves a kitchen ticket's order_id — the
        MAX webhook's chain-notify (app.webhooks.max) only has ticket_id from
        the callback payload, not order_id, and needs it for the notify text.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT order_id FROM kitchen_tickets WHERE id = :id"),
                {"id": UUID(ticket_id)},
            )
            row = result.fetchone()
            return str(row._mapping["order_id"]) if row is not None else None

    async def list_tickets(
        self,
        state_filter: KitchenState | None = None,
    ) -> list[KitchenTicketRead]:
        async with self._session_factory() as session:
            if state_filter is not None:
                result = await session.execute(
                    text(
                        "SELECT id, order_id, state "
                        "FROM kitchen_tickets WHERE state = :state ORDER BY created_at DESC"
                    ),
                    {"state": state_filter.value},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT id, order_id, state "
                        "FROM kitchen_tickets ORDER BY created_at DESC"
                    ),
                )
            rows = result.fetchall()
            return [
                KitchenTicketRead(
                    id=row._mapping["id"],
                    order_id=row._mapping["order_id"],
                    state=KitchenState(row._mapping["state"]),
                )
                for row in rows
            ]


_SESSION_TOOLS = frozenset({
    "write_kitchen_state_queued",
    "write_kitchen_state_preparing",
    "write_kitchen_state_ready",
    "write_kitchen_state_handed_off",
})


def _build_vm(session: AsyncSession) -> _VMProtocol:
    from nano_vm.adapters import MockLLMAdapter
    from nano_vm.vm import ExecutionVM

    from app.db_nano import StoreCursorRepository, get_store

    cursor = StoreCursorRepository(get_store())
    vm = ExecutionVM(
        llm=MockLLMAdapter(""),
        cursor_repository=cursor,
    )
    executor = GovernedToolExecutor(policy=KITCHEN_POLICY_SNAPSHOT)
    for name, fn in _KITCHEN_TOOLS.items():
        governed = _governed_tool(fn, name, executor)
        if name in _SESSION_TOOLS:
            vm.register_tool(name, functools.partial(governed, session=session))
        else:
            vm.register_tool(name, governed)
    return vm


def _governed_tool(
    fn: Callable[..., Any],
    tool_name: str,
    executor: GovernedToolExecutor,
) -> Callable[..., Any]:
    async def wrapper(**kwargs: object) -> Any:
        executor.check(tool_name)
        return await fn(**kwargs)
    return wrapper


_KITCHEN_TOOLS: dict[str, Callable[..., Any]] = {
    "write_kitchen_state_queued": write_kitchen_state_queued,
    "write_kitchen_state_preparing": write_kitchen_state_preparing,
    "write_kitchen_state_ready": write_kitchen_state_ready,
    "write_kitchen_state_handed_off": write_kitchen_state_handed_off,
}
