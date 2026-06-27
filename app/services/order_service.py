from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import async_session_factory
from app.domains.orders.fsm import OrderFSM
from app.domains.orders.models import OrderEvent
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository


class OrderService:
    """Composition root: wires OrderRepository → OrderFSM."""

    def __init__(
        self,
        session_factory: async_sessionmaker = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def handle_event(
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
            return await fsm.handle_event(order_id, event)
