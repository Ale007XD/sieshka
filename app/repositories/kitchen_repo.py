"""app/repositories/kitchen_repo.py — KitchenRepository for KitchenFSM callbacks."""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.kitchen.fsm import KitchenState

logger = logging.getLogger(__name__)


class KitchenRepository:
    """Repository wrapping raw SQL for the kitchen_tickets table.

    write_state() is the ONLY method that executes UPDATE kitchen_tickets SET state=...
    This is the M1-equivalent of the nano-vm terminal tool for kitchen state.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self, entity_id: str) -> KitchenState:
        result = await self._session.execute(
            text("SELECT state FROM kitchen_tickets WHERE id = :id"),
            {"id": UUID(entity_id)},
        )
        row = result.scalar_one()
        return KitchenState(row)

    async def write_state(self, entity_id: str, state: KitchenState) -> None:
        await self._session.execute(
            text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
            {"id": UUID(entity_id), "state": state.value},
        )
        logger.info("KitchenRepository: wrote state %s for ticket %s", state, entity_id)

    async def create(self, order_id: str) -> str:
        """Insert a new kitchen_ticket in NEW state. Returns the new ticket id."""
        result = await self._session.execute(
            text(
                "INSERT INTO kitchen_tickets (order_id, state) "
                "VALUES (:order_id, :state) "
                "RETURNING id"
            ),
            {"order_id": UUID(order_id), "state": KitchenState.NEW.value},
        )
        row = result.one()
        ticket_id = str(row._mapping["id"])
        logger.info("KitchenRepository: created ticket %s for order %s", ticket_id, order_id)
        return ticket_id
