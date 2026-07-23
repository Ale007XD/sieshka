from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

from nano_vm.models import Program, Trace, TraceStatus
from nano_vm.validator import ProgramValidator
from nano_vm_mcp.handlers import GovernedToolExecutor
from opentelemetry import trace as otel_trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import async_session_factory
from app.db_nano import StoreCursorRepository, get_store
from app.domains.orders.models import (
    ORDER_TRANSITIONS,
    CheckoutItem,
    CheckoutRequest,
    OrderCreate,
    OrderEvent,
    OrderItem,
    OrderRead,
    OrderState,
)
from app.fsm.core.base import TransitionResult
from app.policy.policy_snapshot import ORDERS_POLICY_SNAPSHOT
from app.programs.order_programs import (
    EVENT_PROGRAM_MAP,
    build_simple_program,
)
from app.repositories.kitchen_repo import KitchenRepository
from app.repositories.order_repo import OrderRepository
from app.services.menu_service import MenuService
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
_tracer = otel_trace.get_tracer("sieshka")


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
        self._store = get_store()

    def _transition_vm(self, session: AsyncSession) -> _VMProtocol:
        """Return a VM per-transition, bound to the given session.
        
        If a test VM was injected via constructor, return it as-is
        (the test is responsible for its session wiring).
        Otherwise build a fresh VM with session-bound tools.
        """
        if self._vm is not None:
            return self._vm
        return _build_vm(session)

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
                items=_coerce_items(
                    items_val if isinstance(items_val, list) else json.loads(items_val)
                ),
                delivery_address=row._mapping["delivery_address"],
                trace_id=row._mapping.get("trace_id"),
            )

    async def create_order_from_checkout(
        self,
        data: CheckoutRequest,
        customer_id: UUID,
        items: list[OrderItem],
        total_rub: int,
    ) -> OrderRead:
        """Persist a checkout-built order with a typed, price-snapshotted item list.

        The caller (checkout route) is responsible for resolving prices via
        menu_service and computing the server-authoritative total; this method
        only persists what it is given. Never trusts a client-supplied total.
        """
        delivery_address = data.address or ""
        # BUGFIX (2026-07-19): mode="json" is required — plain model_dump()
        # leaves OrderItem.product_id as a uuid.UUID instance, and stdlib
        # json.dumps() has no encoder for UUID, raising TypeError on every
        # checkout. Same class of bug already fixed once in
        # menu_import_service.py (2026-07-15) — model_dump() output going
        # into json.dumps()/a Trace context always needs mode="json".
        items_payload = json.dumps([item.model_dump(mode="json") for item in items])
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO orders "
                    "(customer_id, items, delivery_address, state, delivery_mode, "
                    " zone_id, comment, client_max_uid, total_rub, payment_method) "
                    "VALUES (:customer_id, :items, :delivery_address, :state, "
                    " :delivery_mode, :zone_id, :comment, :client_max_uid, "
                    " :total_rub, :payment_method) "
                    "RETURNING id, customer_id, state, items, delivery_address, trace_id"
                ),
                {
                    "customer_id": customer_id,
                    "items": items_payload,
                    "delivery_address": delivery_address,
                    "state": OrderState.DRAFT.value,
                    "delivery_mode": data.delivery_mode,
                    "zone_id": data.zone_id,
                    "comment": data.comment,
                    "client_max_uid": data.client_max_uid,
                    "total_rub": total_rub,
                    "payment_method": data.payment_method,
                },
            )
            await session.commit()
            row = result.one()
            items_val = row._mapping["items"]
            return OrderRead(
                id=row._mapping["id"],
                customer_id=row._mapping["customer_id"],
                state=OrderState(row._mapping["state"]),
                items=_coerce_items(
                    items_val if isinstance(items_val, list) else json.loads(items_val)
                ),
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

            _report = ProgramValidator(program).validate()
            if not _report.is_valid():
                raise RuntimeError(
                    f"Program '{program.name}' validation failed: {_report.summary()}"
                )

            with _tracer.start_as_current_span(
                "sieshka.order_transition",
                attributes={
                    "order_id": order_id,
                    "event_type": event.value,
                    "program_name": program.name,
                },
            ):
                trace = await self._transition_vm(session).run(program, context=context)

            # Persist trace to SQLite store so receipt viewer works.
            if trace.trace_id:
                self._store.save_trace(
                    trace_id=trace.trace_id,
                    program_id=trace.program_name,
                    status=trace.status.value,
                    steps_count=len(trace.steps),
                    total_cost=trace.total_cost_usd() or 0.0,
                    trace=trace.model_dump(mode="json"),
                )

            if trace.status == TraceStatus.SUCCESS:
                # Persist trace_id so /admin/ui/orders/{id}/receipt works.
                if trace.trace_id:
                    await session.execute(
                        text(
                            "UPDATE orders SET trace_id = :trace_id WHERE id = :order_id"
                        ),
                        {"trace_id": trace.trace_id, "order_id": order_id},
                    )
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
    ) -> Program:
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

    async def get_order(self, order_id: str) -> OrderRead | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, customer_id, state, items, delivery_address, trace_id "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order_id},
            )
            row = result.fetchone()
            if row is None:
                return None
            items_val = row._mapping["items"]
            return OrderRead(
                id=row._mapping["id"],
                customer_id=row._mapping["customer_id"],
                state=OrderState(row._mapping["state"]),
                items=_coerce_items(
                    items_val if isinstance(items_val, list) else json.loads(items_val)
                ),
                delivery_address=row._mapping["delivery_address"],
                trace_id=row._mapping.get("trace_id"),
            )

    async def list_orders(
        self,
        state_filter: OrderState | None = None,
    ) -> list[OrderRead]:
        return await fetch_orders(self._session_factory, state_filter)

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

            _report = ProgramValidator(program).validate()
            if not _report.is_valid():
                raise RuntimeError(
                    f"Program '{program.name}' validation failed: {_report.summary()}"
                )

            with _tracer.start_as_current_span(
                "sieshka.order_transition",
                attributes={
                    "order_id": order_id,
                    "event_type": event.value,
                    "program_name": program.name,
                },
            ):
                trace = await self._transition_vm(session).run(program, context=context)

            # Persist trace to SQLite store so receipt viewer works.
            if trace.trace_id:
                self._store.save_trace(
                    trace_id=trace.trace_id,
                    program_id=trace.program_name,
                    status=trace.status.value,
                    steps_count=len(trace.steps),
                    total_cost=trace.total_cost_usd() or 0.0,
                    trace=trace.model_dump(mode="json"),
                )

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


async def fetch_orders(
    session_factory: async_sessionmaker[AsyncSession],
    state_filter: OrderState | None = None,
) -> list[OrderRead]:
    async with session_factory() as session:
        base_sql = (
            "SELECT o.id, o.customer_id, o.state, o.items, o.delivery_address, "
            "o.delivery_mode, o.payment_method, o.comment, o.trace_id, "
            "c.name AS customer_name, c.phone AS customer_phone "
            "FROM orders o "
            "LEFT JOIN customers c ON c.id = o.customer_id "
        )
        if state_filter is not None:
            result = await session.execute(
                text(base_sql + "WHERE o.state = :state ORDER BY o.created_at DESC"),
                {"state": state_filter.value},
            )
        else:
            result = await session.execute(
                text(base_sql + "ORDER BY o.created_at DESC"),
            )
        rows = result.fetchall()
        return [
            OrderRead(
                id=row._mapping["id"],
                customer_id=row._mapping["customer_id"],
                state=OrderState(row._mapping["state"]),
                items=_coerce_items(
                    row._mapping["items"]
                    if isinstance(row._mapping["items"], list)
                    else json.loads(row._mapping["items"])
                ),
                delivery_address=row._mapping["delivery_address"],
                delivery_mode=row._mapping.get("delivery_mode"),
                payment_method=row._mapping.get("payment_method"),
                comment=row._mapping.get("comment"),
                customer_name=row._mapping.get("customer_name"),
                customer_phone=row._mapping.get("customer_phone"),
                trace_id=row._mapping.get("trace_id"),
            )
            for row in rows
        ]


_SESSION_TOOLS = frozenset({
    "validate_order_items",
    "write_order_state_payment_pending",
    "write_order_state_paid",
    "write_order_state_payment_failed",
    "write_order_state_cooking",
    "reserve_inventory_items",
    "create_kitchen_ticket",
    "transition_order_state",
})


def _build_vm(session: AsyncSession) -> _VMProtocol:
    from nano_vm.adapters import MockLLMAdapter
    from nano_vm.vm import ExecutionVM

    cursor = StoreCursorRepository(get_store())
    vm = ExecutionVM(
        llm=MockLLMAdapter(""),
        cursor_repository=cursor,
    )
    executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
    for name, fn in _ORDER_TOOLS.items():
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


_ORDER_TOOLS: dict[str, Callable[..., Any]] = {
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


def _coerce_items(raw_items: list[object]) -> list[OrderItem]:
    """Coerce a raw JSONB ``items`` column into typed ``OrderItem`` records.

    New checkout orders persist fully-snapshotted OrderItems. Legacy rows
    (and agent-created orders) may carry arbitrary dicts — those are wrapped
    so OrderRead never receives an untyped ``list[dict]`` and downstream
    receipt/admin rendering stays attribute-based under mypy --strict.
    """
    coerced: list[OrderItem] = []
    for raw in raw_items:
        if isinstance(raw, OrderItem):
            coerced.append(raw)
            continue
        if isinstance(raw, dict):
            try:
                coerced.append(OrderItem.model_validate(raw))
                continue
            except Exception:
                pass
            coerced.append(
                OrderItem(
                    product_id=uuid4(),
                    name=str(raw.get("sku", raw.get("product_id", "unknown"))),
                    price_rub=int(raw.get("price_rub", 0)),
                    qty=int(raw.get("qty", 1)),
                )
            )
            continue
        coerced.append(OrderItem(product_id=uuid4(), name="unknown", price_rub=0, qty=1))
    return coerced


def compute_checkout_total(items: list[OrderItem], delivery_mode: str) -> int:
    """Server-authoritative total in RUB.

    Sum(product.price_rub * qty) + flat DELIVERY_FEE (from settings, identical
    to GET /api/config/delivery-fee) when delivery_mode != "pickup", else 0.

    "model output is not execution authority" applied to money — a client
    total is never accepted; this is the only number that counts.
    """
    goods = sum(item.price_rub * item.qty for item in items)
    if delivery_mode == "pickup":
        return goods
    return goods + settings.DELIVERY_FEE


async def resolve_checkout_items(
    checkout_items: list[CheckoutItem],
    menu_service: MenuService | None = None,
) -> list[OrderItem]:
    """Resolve checkout items into price-snapshotted ``OrderItem`` records.

    Prices/names are read ONCE from the live menu and frozen here. They are
    never re-joined to the mutable Product row when the order is later shown.
    """
    menu = menu_service or MenuService()
    resolved: list[OrderItem] = []
    for ci in checkout_items:
        snapshot = await menu.get_product_snapshot(ci.product_id)
        if snapshot is None or snapshot.price_rub is None:
            raise ValueError(f"product {ci.product_id} not found or has no price")
        resolved.append(
            OrderItem(
                product_id=snapshot.product_id,
                name=snapshot.name,
                price_rub=snapshot.price_rub,
                qty=ci.qty,
            )
        )
    return resolved
