"""app/services/inventory_service.py — read-only inventory queries for dashboard."""
from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.inventory.models import InventoryState


class InventoryItemRead(BaseModel):
    sku: str
    name: str
    quantity: int
    state: InventoryState


class InventorySyncResult(BaseModel):
    created: int
    skipped_no_sku: int


class InventoryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def sync_from_menu(self) -> InventorySyncResult:
        """
        Idempotent get_or_create: every active product with a sku assigned
        gets a matching inventory row if one doesn't already exist. Existing
        inventory rows (quantity, state) are never touched — sync only fills
        gaps, it does not overwrite stock levels or re-run on every request.

        Products without a sku are counted, not treated as an error — sku
        assignment on products is a separate, manual admin action (this
        migration added the column nullable on purpose, see
        migrations/015_products_sku.sql).
        """
        async with self._session_factory() as session:
            products = await session.execute(
                text(
                    "SELECT sku, name FROM products "
                    "WHERE is_active = TRUE"
                ),
            )
            rows = products.fetchall()
            skipped_no_sku = sum(1 for row in rows if row._mapping["sku"] is None)
            candidates = [row for row in rows if row._mapping["sku"] is not None]

            created = 0
            for row in candidates:
                sku = row._mapping["sku"]
                name = row._mapping["name"]
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        text(
                            "INSERT INTO inventory (sku, name, quantity, state) "
                            "VALUES (:sku, :name, 0, 'OUT_OF_STOCK') "
                            "ON CONFLICT (sku) DO NOTHING"
                        ),
                        {"sku": sku, "name": name},
                    ),
                )
                if result.rowcount and result.rowcount > 0:
                    created += 1
            await session.commit()
            return InventorySyncResult(created=created, skipped_no_sku=skipped_no_sku)

    async def set_quantity(self, sku: str, quantity: int) -> InventoryItemRead:
        """
        Admin inline-edit: sets an absolute quantity for one sku and
        recomputes state from the new quantity in the same transaction, so
        `state` never goes stale relative to `quantity` (sprint_inventory_
        restock_inline, 2026-08). Raises ValueError if sku not found —
        caller (the HTTP route) is responsible for turning that into a 404.
        """
        from app.tools.inventory_tools import set_inventory_quantity, set_inventory_state

        async with self._session_factory() as session:
            await set_inventory_quantity(session, sku=sku, quantity=quantity)
            await set_inventory_state(session, sku=sku)
            await session.commit()

            result = await session.execute(
                text("SELECT sku, name, quantity, state FROM inventory WHERE sku = :sku"),
                {"sku": sku},
            )
            row = result.fetchone()
            if row is None:
                raise RuntimeError(f"sku {sku!r} vanished between write and read-back")
            return InventoryItemRead(
                sku=row._mapping["sku"],
                name=row._mapping["name"],
                quantity=row._mapping["quantity"],
                state=InventoryState(row._mapping["state"]),
            )

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
