"""app/services/inventory_service.py — read-only inventory queries for dashboard."""
from __future__ import annotations

import csv
import io
from datetime import date
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


class MovementsSummary(BaseModel):
    """Daily in/out totals from inventory_movements, for the stats chart
    (sprint_inventory_stats_viz, 2026-08). `restocked` = sum of positive
    deltas that day (RESTOCK_MANUAL/RESTOCK_AGENT/SYNC_INIT/positive
    ADJUSTMENT); `sold` = sum of |negative deltas| that day (SALE/negative
    ADJUSTMENT) — sign-flipped so both series plot as positive bars/lines,
    the direction is carried by which series it's in, not the sign."""

    labels: list[str]  # ISO date strings, one per day in range
    restocked: list[int]
    sold: list[int]


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

    async def movements_summary(
        self, from_date: date | None = None, to_date: date | None = None
    ) -> MovementsSummary:
        """Daily restocked-vs-sold totals for the stats chart. Both bounds
        optional — omitted from_date means "since the beginning", omitted
        to_date means "through today"."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT "
                    "  DATE(created_at) AS day, "
                    "  SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS in_qty, "
                    "  SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS out_qty "
                    "FROM inventory_movements "
                    "WHERE (:from_date IS NULL OR created_at >= :from_date) "
                    "  AND (:to_date IS NULL OR created_at < :to_date + INTERVAL '1 day') "
                    "GROUP BY DATE(created_at) "
                    "ORDER BY day"
                ),
                {"from_date": from_date, "to_date": to_date},
            )
            rows = result.fetchall()
            return MovementsSummary(
                labels=[row._mapping["day"].isoformat() for row in rows],
                restocked=[int(row._mapping["in_qty"]) for row in rows],
                sold=[int(row._mapping["out_qty"]) for row in rows],
            )

    async def export_movements_csv(
        self, from_date: date | None = None, to_date: date | None = None
    ) -> str:
        """Row-level CSV of inventory_movements in the given period (both
        bounds optional, same semantics as movements_summary)."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT sku, delta, reason, source_type, source_id, "
                    "below_zero, created_at "
                    "FROM inventory_movements "
                    "WHERE (:from_date IS NULL OR created_at >= :from_date) "
                    "  AND (:to_date IS NULL OR created_at < :to_date + INTERVAL '1 day') "
                    "ORDER BY created_at"
                ),
                {"from_date": from_date, "to_date": to_date},
            )
            rows = result.fetchall()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["sku", "delta", "reason", "source_type", "source_id", "below_zero", "created_at"]
        )
        for row in rows:
            m = row._mapping
            writer.writerow(
                [
                    m["sku"],
                    m["delta"],
                    m["reason"],
                    m["source_type"] or "",
                    m["source_id"] or "",
                    m["below_zero"],
                    m["created_at"].isoformat(),
                ]
            )
        return buffer.getvalue()
