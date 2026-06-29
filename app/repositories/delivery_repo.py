"""app/repositories/delivery_repo.py — DeliveryRepository for DeliveryFSM callbacks."""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.delivery.fsm import DeliveryState

logger = logging.getLogger(__name__)


class DeliveryRepository:
    """Repository wrapping raw SQL for the delivery_tasks table.

    write_state() is the ONLY method that executes UPDATE delivery_tasks SET state=...
    This is the M1-equivalent of the nano-vm terminal tool for delivery state.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self, entity_id: str) -> DeliveryState:
        result = await self._session.execute(
            text("SELECT state FROM delivery_tasks WHERE id = :id"),
            {"id": UUID(entity_id)},
        )
        row = result.scalar_one()
        return DeliveryState(row)

    async def write_state(self, entity_id: str, state: DeliveryState) -> None:
        await self._session.execute(
            text("UPDATE delivery_tasks SET state = :state WHERE id = :id"),
            {"id": UUID(entity_id), "state": state.value},
        )
        logger.info("DeliveryRepository: wrote state %s for task %s", state, entity_id)

    async def create(self, order_id: str) -> str:
        """Insert a new delivery_task in UNASSIGNED state. Returns the new task id."""
        result = await self._session.execute(
            text(
                "INSERT INTO delivery_tasks (order_id, state) "
                "VALUES (:order_id, :state) "
                "RETURNING id"
            ),
            {"order_id": UUID(order_id), "state": DeliveryState.UNASSIGNED.value},
        )
        row = result.one()
        task_id = str(row._mapping["id"])
        logger.info("DeliveryRepository: created task %s for order %s", task_id, order_id)
        return task_id
