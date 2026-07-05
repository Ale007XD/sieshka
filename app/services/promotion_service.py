"""app/services/promotion_service.py — read-only promotion queries for dashboard."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.promotions.models import PromotionState


class PromotionRead(BaseModel):
    id: UUID
    name: str
    discount: float
    state: PromotionState


class PromotionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def list_promotions(self) -> list[PromotionRead]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, name, discount, state "
                    "FROM promotions ORDER BY created_at DESC"
                ),
            )
            rows = result.fetchall()
            return [
                PromotionRead(
                    id=row._mapping["id"],
                    name=row._mapping["name"],
                    discount=float(row._mapping["discount"]),
                    state=PromotionState(row._mapping["state"]),
                )
                for row in rows
            ]
