from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.orders.fsm import OrderFSM
from app.domains.orders.models import OrderCreate, OrderEvent, OrderRead, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.kitchen_repo import KitchenRepository
from app.repositories.order_repo import OrderRepository

logger = logging.getLogger(__name__)


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
    """Composition root: wires OrderRepository -> OrderFSM + PolicyProvider stub."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

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
            fsm = OrderFSM(
                state_reader=repo.get_state,
                state_writer=repo.write_state,
            )

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

            result = await fsm.handle_event(order_id, event)
            if result.success and result.new_state == OrderState.COOKING:
                await self._on_cooking(session, order_id)
            await session.commit()
            return result

    async def _on_cooking(
        self,
        session: AsyncSession,
        order_id: str,
    ) -> None:
        """Cross-domain orchestration: order COOKING → auto-create kitchen_ticket (NEW state).

        Single PG transaction — same session, commit handled by caller.
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
        """Direct FSM call (bypasses PolicyProvider).
        Used internally when policy is not needed.
        """
        async with self._session_factory() as session:
            repo = OrderRepository(session)
            fsm = OrderFSM(
                state_reader=repo.get_state,
                state_writer=repo.write_state,
            )
            result = await fsm.handle_event(order_id, event)
            await session.commit()
            return result
