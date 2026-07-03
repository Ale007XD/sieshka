"""
app/tools/inventory_tools.py — nano-vm Tools for inventory Programs.
M3+: registered with GovernedToolExecutor.
Session injected via functools.partial at VM-construction time
(not through **kwargs — Trace/Receipt contract must stay JSON-serializable).

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for CONDITION steps — not string literals
  - PG transaction: read + write inside single transaction at transition boundary,
    NOT at tool boundary (commit/rollback handled by the calling service)
  - session is a named first parameter (not **kwargs) — closure-injected by
    functools.partial in _build_vm(), never serialised in Trace/Receipt
  - Terminal TOOL step failure propagation: increment_inventory/set_inventory_state
    are terminal writers with no downstream CONDITION consumer — raise on error,
    never return an ERROR sentinel string (CONSTRAINTS.md 2026-07-02)
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def check_inventory_stock(
    session: AsyncSession, sku: str, **kwargs: object
) -> int:
    """Returns current quantity for a SKU. Numeric sentinel for ASTEngine."""
    from sqlalchemy import text as sql_text

    result = await session.execute(
        sql_text("SELECT quantity FROM inventory WHERE sku = :sku"),
        {"sku": sku},
    )
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning("check_inventory_stock: sku=%s not found", sku)
        return 0
    qty = int(row)
    logger.info("check_inventory_stock: sku=%s quantity=%d", sku, qty)
    return qty


async def decrement_inventory(
    session: AsyncSession, sku: str, quantity: int = 1, **kwargs: object
) -> int:
    """
    Decrements inventory by quantity.
    Returns 1 if successful, 0 if insufficient stock.
    Numeric sentinel for ASTEngine.
    """
    from sqlalchemy import text as sql_text

    row = await session.execute(
        sql_text("SELECT quantity FROM inventory WHERE sku = :sku FOR UPDATE"),
        {"sku": sku},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.warning("decrement_inventory: sku=%s not found", sku)
        return 0
    if int(current) < quantity:
        logger.warning(
            "decrement_inventory: insufficient stock sku=%s (have %d, need %d)",
            sku, int(current), quantity,
        )
        return 0
    await session.execute(
        sql_text(
            "UPDATE inventory SET quantity = quantity - :qty WHERE sku = :sku"
        ),
        {"sku": sku, "qty": quantity},
    )
    logger.info("decrement_inventory: sku=%s qty=%d", sku, quantity)
    return 1


async def increment_inventory(
    session: AsyncSession, sku: str, quantity: int = 1, **kwargs: object
) -> str:
    """Terminal tool: increments inventory by quantity (restock)."""  # terminal-tool
    from sqlalchemy import text as sql_text

    row = await session.execute(
        sql_text("SELECT id FROM inventory WHERE sku = :sku FOR UPDATE"),
        {"sku": sku},
    )
    if row.scalar_one_or_none() is None:
        logger.warning("increment_inventory: sku=%s not found", sku)
        raise ValueError(f"sku not found: {sku}")
    await session.execute(
        sql_text(
            "UPDATE inventory SET quantity = quantity + :qty WHERE sku = :sku"
        ),
        {"sku": sku, "qty": quantity},
    )
    logger.info("increment_inventory: sku=%s qty=%d", sku, quantity)
    return "OK"


async def set_inventory_state(
    session: AsyncSession, sku: str, **kwargs: object
) -> str:
    """
    Terminal tool: recalculates and writes inventory state
    based on current quantity thresholds. Atomic PG write.
    """  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.inventory.models import InventoryState

    row = await session.execute(
        sql_text("SELECT quantity FROM inventory WHERE sku = :sku FOR UPDATE"),
        {"sku": sku},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.warning("set_inventory_state: sku=%s not found", sku)
        raise ValueError(f"sku not found: {sku}")
    qty = int(current)
    if qty <= 0:
        new_state = InventoryState.OUT_OF_STOCK.value
    elif qty < 5:
        new_state = InventoryState.CRITICAL.value
    elif qty < 20:
        new_state = InventoryState.LOW_STOCK.value
    else:
        new_state = InventoryState.AVAILABLE.value
    await session.execute(
        sql_text("UPDATE inventory SET state = :state WHERE sku = :sku"),
        {"sku": sku, "state": new_state},
    )
    logger.info("set_inventory_state: sku=%s state=%s", sku, new_state)
    return "OK"
