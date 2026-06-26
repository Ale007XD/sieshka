"""app/repositories/order_repo.py — OrderRepository for OrderFSM callbacks."""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.orders.models import OrderState

logger = logging.getLogger(__name__)


class OrderRepository:
    """Repository wrapping raw SQL for the orders table.

    write_state() is the ONLY method that executes UPDATE orders SET state=...
    This is the M1-equivalent of the nano-vm terminal tool for order state.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self, entity_id: str) -> OrderState:
        result = await self._session.execute(
            text("SELECT state FROM orders WHERE id = :id"),
            {"id": UUID(entity_id)},
        )
        row = result.scalar_one()
        return OrderState(row)

    async def write_state(self, entity_id: str, state: OrderState) -> None:
        await self._session.execute(
            text("UPDATE orders SET state = :state WHERE id = :id"),
            {"id": UUID(entity_id), "state": state.value},
        )
        await self._session.commit()
        logger.info("OrderRepository: wrote state %s for order %s", state, entity_id)
