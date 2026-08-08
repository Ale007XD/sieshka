"""app/services/inventory_service.py — read-only inventory queries for dashboard."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.inventory.models import InventoryState

logger = logging.getLogger(__name__)


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
                    "WHERE (CAST(:from_date AS DATE) IS NULL "
                    "       OR created_at >= CAST(:from_date AS DATE)) "
                    "  AND (CAST(:to_date AS DATE) IS NULL "
                    "       OR created_at < CAST(:to_date AS DATE) + INTERVAL '1 day') "
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
        bounds optional, same semantics as movements_summary).

        price_rub/sale_amount are populated ONLY for SALE-reason rows —
        restock/adjustment/sync movements have no sale price concept.
        price_rub is resolved from the ORDER's item snapshot (orders.items,
        frozen at order-creation time — see OrderItem's documented
        immutability contract), NOT the product's current price. A later
        price change must not retroactively alter what an already-completed
        sale's CSV export shows it sold for.

        Ends with two summary sections: daily sale_amount totals, then a
        grand total — both computed only over rows where sale_amount was
        successfully resolved (2026-08, sprint_inventory_stats_viz follow-up).
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT sku, delta, reason, source_type, source_id, "
                    "below_zero, created_at "
                    "FROM inventory_movements "
                    "WHERE (CAST(:from_date AS DATE) IS NULL "
                    "       OR created_at >= CAST(:from_date AS DATE)) "
                    "  AND (CAST(:to_date AS DATE) IS NULL "
                    "       OR created_at < CAST(:to_date AS DATE) + INTERVAL '1 day') "
                    "ORDER BY created_at"
                ),
                {"from_date": from_date, "to_date": to_date},
            )
            rows = result.fetchall()

            sale_rows = [
                row for row in rows
                if row._mapping["reason"] == "SALE" and row._mapping["source_id"]
            ]

            sku_to_product_id: dict[str, str] = {}
            items_by_order: dict[str, list[dict[str, Any]]] = {}
            if sale_rows:
                skus = {row._mapping["sku"] for row in sale_rows}
                sku_rows = await session.execute(
                    text("SELECT id, sku FROM products WHERE sku = ANY(:skus)"),
                    {"skus": list(skus)},
                )
                sku_to_product_id = {
                    r._mapping["sku"]: str(r._mapping["id"]) for r in sku_rows.fetchall()
                }

                order_ids = {row._mapping["source_id"] for row in sale_rows}
                valid_order_uuids = []
                for oid in order_ids:
                    try:
                        valid_order_uuids.append(UUID(oid))
                    except ValueError:
                        logger.warning(
                            "export_movements_csv: source_id %r is not a valid UUID, "
                            "skipping price resolution for its rows", oid,
                        )
                if valid_order_uuids:
                    order_rows = await session.execute(
                        text("SELECT id, items FROM orders WHERE id = ANY(:ids)"),
                        {"ids": valid_order_uuids},
                    )
                    for r in order_rows.fetchall():
                        raw_items = r._mapping["items"]
                        items = (
                            raw_items if isinstance(raw_items, list) else json.loads(raw_items)
                        )
                        items_by_order[str(r._mapping["id"])] = items

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "sku", "delta", "reason", "source_type", "source_id", "below_zero",
                "created_at", "price_rub", "sale_amount",
            ]
        )

        daily_totals: dict[str, int] = {}
        grand_total = 0

        for row in rows:
            m = row._mapping
            price_rub: int | None = None
            sale_amount: int | None = None

            if m["reason"] == "SALE" and m["source_id"]:
                product_id = sku_to_product_id.get(m["sku"])
                order_items = items_by_order.get(m["source_id"], [])
                if product_id is not None:
                    matched = next(
                        (i for i in order_items if str(i.get("product_id")) == product_id),
                        None,
                    )
                    if matched is not None:
                        price_rub = int(matched["price_rub"])
                        sale_amount = price_rub * abs(int(m["delta"]))
                        day_key = m["created_at"].date().isoformat()
                        daily_totals[day_key] = daily_totals.get(day_key, 0) + sale_amount
                        grand_total += sale_amount

            writer.writerow(
                [
                    m["sku"],
                    m["delta"],
                    m["reason"],
                    m["source_type"] or "",
                    m["source_id"] or "",
                    m["below_zero"],
                    m["created_at"].isoformat(),
                    price_rub if price_rub is not None else "",
                    sale_amount if sale_amount is not None else "",
                ]
            )

        writer.writerow([])
        writer.writerow(["Daily sales totals"])
        writer.writerow(["date", "sale_amount"])
        for day_key in sorted(daily_totals):
            writer.writerow([day_key, daily_totals[day_key]])

        writer.writerow([])
        writer.writerow(["Grand total", grand_total])

        return buffer.getvalue()
