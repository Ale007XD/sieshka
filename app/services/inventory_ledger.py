"""
app/services/inventory_ledger.py — shared write helper for inventory_movements.

Single source of truth for the INSERT shape so inventory_tools.py (restock
paths) and order_tools.py (sale path, wired in sprint_inventory_sale_decrement)
don't each hand-roll the same statement. Caller controls the transaction —
this function only executes an INSERT on the session it's given, it never
opens or commits one itself (same session closure-injection rule as every
other DB-writing tool, see CONSTRAINTS.md Tool-authoring: side-effect session
boundary).
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MovementReason = Literal[
    "RESTOCK_MANUAL", "RESTOCK_AGENT", "SALE", "ADJUSTMENT", "SYNC_INIT",
]


async def record_movement(
    session: AsyncSession,
    sku: str,
    delta: int,
    reason: MovementReason,
    source_type: str | None = None,
    source_id: str | None = None,
    below_zero: bool = False,
) -> None:
    """Writes one inventory_movements row. Caller commits."""
    await session.execute(
        text(
            "INSERT INTO inventory_movements "
            "(sku, delta, reason, source_type, source_id, below_zero) "
            "VALUES (:sku, :delta, :reason, :source_type, :source_id, :below_zero)"
        ),
        {
            "sku": sku,
            "delta": delta,
            "reason": reason,
            "source_type": source_type,
            "source_id": source_id,
            "below_zero": below_zero,
        },
    )
