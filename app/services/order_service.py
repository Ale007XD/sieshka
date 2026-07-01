from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from nano_vm.models import Program, Trace, TraceStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.db_nano import StoreCursorRepository, get_store
from app.domains.orders.models import (
    ORDER_TRANSITIONS,
    OrderCreate,
    OrderEvent,
    OrderRead,
    OrderState,
)
from app.fsm.core.base import TransitionResult
from app.programs.order_programs import (
    EVENT_PROGRAM_MAP,
    build_simple_program,
)
from app.repositories.kitchen_repo import KitchenRepository
from app.repositories.order_repo import OrderRepository
from app.tools.order_tools import (
    create_kitchen_ticket,
    log_validation_failure,
    notify_inventory_insufficient,
    reserve_inventory_items,
    transition_order_state,
    validate_order_items,
    write_order_state_cooking,
    write_order_state_paid,
    write_order_state_payment_failed,
    write_order_state_payment_pending,
    yookassa_create_payment,
    yookassa_verify_payment,
)

logger = logging.getLogger(__name__)


class _VMProtocol(Protocol):
    """Minimal protocol for ExecutionVM duck-typing."""
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class PolicyProvider:
    """M1 stub: always-allow policy.
    M2+: replaced with real policy evaluation (roles, business rules).
    ADR-004: PolicyProvider is called from Application Service, not FSM.
    """

    async def can_transition(
        self,
        order_id: str,
        event: OrderEvent,
        current_state: OrderState,
    ) -> bool:
        return True


class OrderService:
    """Composition root: wires ExecutionVM -> Programs -> Tools -> Repository.

    OrderFSM retained (deprecated) in app.domains.orders.fsm for rollback safety.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        vm: _VMProtocol | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vm = vm

    def _get_vm(self) -> _VMProtocol:
        if self._vm is None:
            self._vm = _build_vm()
        return self._vm

    async def create_order(self, data: OrderCreate) -> OrderRead:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO orders (customer_id, items, delivery_address, state) "
                    "VALUES (:customer_id, :items, :delivery_address, :state) "
                    "RETURNING id, customer_id, state, items, delivery_address, trace_id"
                ),
                {
                    "customer_id": data.customer_id,
                    "items": json.dumps(data.items),
                    "delivery_address": data.delivery_address,
                    "state": OrderState.DRAFT.value,
                },
            )
            await session.commit()
            row = result.one()
            items_val = row._mapping["items"]
            return OrderRead(
                id=row._mapping["id"],
                customer_id=row._mapping["customer_id"],
                state=OrderState(row._mapping["state"]),
                items=items_val if isinstance(items_val, list) else json.loads(items_val),
                delivery_address=row._mapping["delivery_address"],
                trace_id=row._mapping.get("trace_id"),
            )

    async def transition_order(
        self,
        order_id: str,
        event: OrderEvent,
    ) -> TransitionResult:
        async with self._session_factory() as session:
            repo = OrderRepository(session)
            current_state = await repo.get_state(order_id)

            policy = PolicyProvider()
            if not await policy.can_transition(order_id, event, current_state):
                logger.warning(
                    "PolicyProvider: blocked %s event=%s from=%s",
                    order_id, event, current_state,
                )
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=event,
                    reason="Blocked by policy",
                )

            allowed = ORDER_TRANSITIONS.get(current_state, {})
            if event not in allowed:
                logger.warning(
                    "OrderService: rejected %s event=%s from state=%s",
                    order_id, event, current_state,
                )
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=event,
                    reason=f"Event {event!r} not allowed from state {current_state!r}",
                )

            new_state = allowed[event]
            program = self._select_program(event, current_state, new_state)
            context = {"order_id": order_id}
            if event == OrderEvent.CANCEL:
                context["from_state"] = current_state.value

            trace = await self._get_vm().run(program, context=context)

            if trace.status == TraceStatus.SUCCESS:
                if new_state == OrderState.COOKING and event == OrderEvent.START_COOKING:
                    pass
                elif new_state == OrderState.COOKING:
                    await self._on_cooking(session, order_id)
                await session.commit()
                return TransitionResult(
                    success=True,
                    new_state=new_state,
                    rejected_event=None,
                    reason=None,
                )

            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=event,
                reason=trace.error or "Execution failed",
            )

    def _select_program(
        self,
        event: OrderEvent,
        current_state: OrderState,
        new_state: OrderState,
    ) -> object:
        """Select the appropriate Program for the transition.
        
        Uses full business-logic programs for complex transitions (START_COOKING),
        simple single-step programs for basic state writes.
        
        REQUEST_PAYMENT and PAYMENT_CONFIRMED are NOT dispatched from here —
        they are used by PaymentService which handles YooKassa integration separately.
        """

        if event == OrderEvent.START_COOKING:
            return EVENT_PROGRAM_MAP["START_COOKING"]

        if event.value in EVENT_PROGRAM_MAP and event.value in (
            "REQUEST_PAYMENT", "PAYMENT_CONFIRMED",
        ):
            return build_simple_program(event.value, current_state.value, new_state.value)

        if event.value in EVENT_PROGRAM_MAP:
            return EVENT_PROGRAM_MAP[event.value]

        return build_simple_program(event.value, current_state.value, new_state.value)

    async def _on_cooking(
        self,
        session: AsyncSession,
        order_id: str,
    ) -> None:
        """Cross-domain orchestration: order COOKING → auto-create kitchen_ticket (NEW state).
        Only called when the transition did NOT use PROGRAM_START_COOKING
        (which already creates the ticket within its program steps).
        """
        kitchen_repo = KitchenRepository(session)
        await kitchen_repo.create(order_id)

    async def list_orders(
        self,
        state_filter: OrderState | None = None,
    ) -> list[OrderRead]:
        async with self._session_factory() as session:
            if state_filter is not None:
                result = await session.execute(
                    text(
                        "SELECT id, customer_id, state, items, delivery_address, trace_id "
                        "FROM orders WHERE state = :state ORDER BY created_at DESC"
                    ),
                    {"state": state_filter.value},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT id, customer_id, state, items, delivery_address, trace_id "
                        "FROM orders ORDER BY created_at DESC"
                    ),
                )
            rows = result.fetchall()
            return [
                OrderRead(
                    id=row._mapping["id"],
                    customer_id=row._mapping["customer_id"],
                    state=OrderState(row._mapping["state"]),
                    items=row._mapping["items"]
                    if isinstance(row._mapping["items"], list)
                    else json.loads(row._mapping["items"]),
                    delivery_address=row._mapping["delivery_address"],
                    trace_id=row._mapping.get("trace_id"),
                )
                for row in rows
            ]

    async def handle_event(
        self,
        order_id: str,
        event: OrderEvent,
    ) -> TransitionResult:
        """Direct VM call (bypasses PolicyProvider)."""
        async with self._session_factory() as session:
            repo = OrderRepository(session)
            current_state = await repo.get_state(order_id)

            allowed = ORDER_TRANSITIONS.get(current_state, {})
            if event not in allowed:
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=event,
                    reason=f"Event {event!r} not allowed from state {current_state!r}",
                )

            new_state = allowed[event]
            program = self._select_program(event, current_state, new_state)
            context = {"order_id": order_id}
            if event == OrderEvent.CANCEL:
                context["from_state"] = current_state.value

            trace = await self._get_vm().run(program, context=context)

            if trace.status == TraceStatus.SUCCESS:
                await session.commit()
                return TransitionResult(
                    success=True,
                    new_state=new_state,
                    rejected_event=None,
                    reason=None,
                )

            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=event,
                reason=trace.error or "Execution failed",
            )


def _build_vm() -> _VMProtocol:
    from nano_vm.adapters import MockLLMAdapter
    from nano_vm.vm import ExecutionVM

    cursor = StoreCursorRepository(get_store())
    vm = ExecutionVM(
        llm=MockLLMAdapter(""),
        cursor_repository=cursor,
    )
    for name, fn in _ORDER_TOOLS.items():
        vm.register_tool(name, fn)
    return vm  # type: ignore[no-any-return]  # ExecutionVM type obscured by --follow-imports=skip


_ORDER_TOOLS = {
    "validate_order_items": validate_order_items,
    "yookassa_create_payment": yookassa_create_payment,
    "yookassa_verify_payment": yookassa_verify_payment,
    "write_order_state_payment_pending": write_order_state_payment_pending,
    "write_order_state_paid": write_order_state_paid,
    "write_order_state_payment_failed": write_order_state_payment_failed,
    "write_order_state_cooking": write_order_state_cooking,
    "reserve_inventory_items": reserve_inventory_items,
    "create_kitchen_ticket": create_kitchen_ticket,
    "log_validation_failure": log_validation_failure,
    "notify_inventory_insufficient": notify_inventory_insufficient,
    "transition_order_state": transition_order_state,
}
