"""app/services/inventory_service.py — read-only inventory queries for dashboard."""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.inventory.models import InventoryState


class InventoryItemRead(BaseModel):
    sku: str
    name: str
    quantity: int
    state: InventoryState


class InventoryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def list_inventory(self) -> list[InventoryItemRead]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT sku, name, quantity, state "
                    "FROM inventory ORDER BY sku"
                ),
            )
            rows = result.fetchall()
            return [
                InventoryItemRead(
                    sku=row._mapping["sku"],
                    name=row._mapping["name"],
                    quantity=row._mapping["quantity"],
                    state=InventoryState(row._mapping["state"]),
                )
                for row in rows
            ]
