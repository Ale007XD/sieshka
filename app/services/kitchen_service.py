from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.kitchen.fsm import KitchenEvent, KitchenFSM, KitchenState
from app.fsm.core.base import TransitionResult
from app.repositories.kitchen_repo import KitchenRepository

logger = logging.getLogger(__name__)


class KitchenTicketRead(BaseModel):
    id: UUID
    order_id: UUID
    state: KitchenState


class KitchenService:
    """Composition root: wires KitchenRepository -> KitchenFSM."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory() as session:
            repo = KitchenRepository(session)
            fsm = KitchenFSM(
                state_reader=repo.get_state,
                state_writer=repo.write_state,
            )
            result = await fsm.handle_event(ticket_id, event)
            if result.success:
                await session.commit()
            else:
                return result

        # Cross-domain: HANDED_OFF → advance order to PACKING, then
        # auto-close pickup orders (delivery_mode='pickup').
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
