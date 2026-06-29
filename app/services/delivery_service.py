from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.delivery.fsm import DeliveryEvent, DeliveryFSM, DeliveryState
from app.fsm.core.base import TransitionResult
from app.repositories.delivery_repo import DeliveryRepository

logger = logging.getLogger(__name__)


class DeliveryTaskRead(BaseModel):
    id: UUID
    order_id: UUID
    state: DeliveryState
    courier_id: UUID | None = None


class DeliveryService:
    """Composition root: wires DeliveryRepository -> DeliveryFSM."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def create_task(self, order_id: str) -> DeliveryTaskRead:
        async with self._session_factory() as session:
            repo = DeliveryRepository(session)
            task_id = await repo.create(order_id)
            await session.commit()
            return DeliveryTaskRead(
                id=UUID(task_id),
                order_id=UUID(order_id),
                state=DeliveryState.UNASSIGNED,
            )

    async def transition_task(
        self,
        task_id: str,
        event: DeliveryEvent,
    ) -> TransitionResult:
        async with self._session_factory() as session:
            repo = DeliveryRepository(session)
            fsm = DeliveryFSM(
                state_reader=repo.get_state,
                state_writer=repo.write_state,
            )
            result = await fsm.handle_event(task_id, event)
            await session.commit()
            return result

    async def list_tasks(
        self,
        state_filter: DeliveryState | None = None,
    ) -> list[DeliveryTaskRead]:
        async with self._session_factory() as session:
            if state_filter is not None:
                result = await session.execute(
                    text(
                        "SELECT id, order_id, state "
                        "FROM delivery_tasks WHERE state = :state ORDER BY created_at DESC"
                    ),
                    {"state": state_filter.value},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT id, order_id, state "
                        "FROM delivery_tasks ORDER BY created_at DESC"
                    ),
                )
            rows = result.fetchall()
            return [
                DeliveryTaskRead(
                    id=row._mapping["id"],
                    order_id=row._mapping["order_id"],
                    state=DeliveryState(row._mapping["state"]),
                )
                for row in rows
            ]
