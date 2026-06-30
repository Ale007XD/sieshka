"""
app/tools/inventory_tools.py — nano-vm Tools for inventory Programs.
M3+: registered with GovernedToolExecutor.

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for CONDITION steps
  - PG transaction: read + write inside single transaction in terminal tools
"""
from __future__ import annotations

import logging

from app.db import async_session_factory

logger = logging.getLogger(__name__)


async def check_inventory_stock(sku: str, **kwargs: object) -> int:
    """Returns current quantity for a SKU. Numeric sentinel for ASTEngine."""
    from sqlalchemy import text as sql_text

    async with async_session_factory() as session:
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


async def decrement_inventory(sku: str, quantity: int = 1, **kwargs: object) -> int:
    """
    Decrements inventory by quantity.
    Returns 1 if successful, 0 if insufficient stock.
    Numeric sentinel for ASTEngine.
    """
    from sqlalchemy import text as sql_text

    async with async_session_factory() as session:
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
        await session.commit()
    logger.info("decrement_inventory: sku=%s qty=%d", sku, quantity)
    return 1


async def increment_inventory(sku: str, quantity: int = 1, **kwargs: object) -> str:
    """Increments inventory by quantity (restock). Returns OK/ERROR."""
    from sqlalchemy import text as sql_text

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT id FROM inventory WHERE sku = :sku FOR UPDATE"),
            {"sku": sku},
        )
        if row.scalar_one_or_none() is None:
            logger.warning("increment_inventory: sku=%s not found", sku)
            return "ERROR"
        await session.execute(
            sql_text(
                "UPDATE inventory SET quantity = quantity + :qty WHERE sku = :sku"
            ),
            {"sku": sku, "qty": quantity},
        )
        await session.commit()
    logger.info("increment_inventory: sku=%s qty=%d", sku, quantity)
    return "OK"


async def set_inventory_state(sku: str, **kwargs: object) -> str:
    """
    Terminal tool: recalculates and writes inventory state
    based on current quantity thresholds. Returns OK/ERROR.
    """  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.inventory.models import InventoryState

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT quantity FROM inventory WHERE sku = :sku FOR UPDATE"),
            {"sku": sku},
        )
        current = row.scalar_one_or_none()
        if current is None:
            logger.warning("set_inventory_state: sku=%s not found", sku)
            return "ERROR"
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
        await session.commit()
    logger.info("set_inventory_state: sku=%s state=%s", sku, new_state)
    return "OK"
