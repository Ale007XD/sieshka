"""
app/tools/order_tools.py — nano-vm Tools for order Programs.
M3+: registered with GovernedToolExecutor.
Session injected via functools.partial at VM-construction time
(not through **kwargs — Trace/Receipt contract must stay JSON-serializable).

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for CONDITION steps — not string literals
  - PG transaction: read + write inside single transaction at transition boundary,
    NOT at tool boundary (commit/rollback handled by OrderService)
  - External calls (HTTP, MQ) FORBIDDEN inside PG transaction block
  - session is a named first parameter (not **kwargs) — closure-injected by
    functools.partial in _build_vm(), never serialised in Trace/Receipt
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def validate_order_items(session: AsyncSession, order_id: str, **kwargs: object) -> int:
    """Returns 1 if valid, 0 if invalid. Numeric sentinel for ASTEngine."""
    from sqlalchemy import text as sql_text

    result = await session.execute(
        sql_text("SELECT items FROM orders WHERE id = :id"),
        {"id": UUID(order_id)},
    )
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning("validate_order_items: order %s not found", order_id)
        return 0
    items = row if isinstance(row, list) else json.loads(row)
    if not items:
        logger.warning("validate_order_items: order %s has no items", order_id)
        return 0
    logger.info("validate_order_items: order_id=%s valid (%d items)", order_id, len(items))
    return 1


# ---------------------------------------------------------------------------
# YooKassa (M2+)
# External HTTP call — OUTSIDE terminal tool, NOT inside PG transaction
# ---------------------------------------------------------------------------


async def yookassa_create_payment(
    order_id: str,
    amount: str,
    currency: str = "RUB",
    **kwargs: object,
) -> str:
    """
    Creates YooKassa payment. Returns payment_id.
    metadata contains trace_id for webhook resume (ADR-003).
    """
    from app.config import settings

    shop_id = settings.YOOKASSA_SHOP_ID
    secret_key = settings.YOOKASSA_SECRET_KEY
    return_url = settings.YOOKASSA_RETURN_URL
    trace_id = str(kwargs.get("trace_id", ""))

    if not shop_id or not secret_key:
        logger.warning("YOOKASSA_SHOP_ID or YOOKASSA_SECRET_KEY not set — using placeholder")
        return "payment_placeholder_id"

    payload = {
        "amount": {"value": amount, "currency": currency},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": f"Order {order_id}",
        "metadata": {"order_id": order_id, "trace_id": trace_id},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json=payload,
            auth=(shop_id, secret_key),
            headers={"Idempotence-Key": f"order_{order_id}"},
        )
        resp.raise_for_status()
        data = resp.json()
        payment_id = data.get("id", "payment_placeholder_id")
        logger.info(
            "yookassa_create_payment: order_id=%s payment_id=%s", order_id, payment_id
        )
        return str(payment_id)


async def yookassa_verify_payment(
    order_id: str,
    payment_id: str,
    **kwargs: object,
) -> int:
    """Returns 1 if confirmed, 0 if failed. Numeric sentinel."""
    from app.config import settings

    shop_id = settings.YOOKASSA_SHOP_ID
    secret_key = settings.YOOKASSA_SECRET_KEY

    if not shop_id or not secret_key:
        logger.warning("YOOKASSA credentials not set — returning 1 (stub)")
        return 1

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.yookassa.ru/v3/payments/{payment_id}",
            auth=(shop_id, secret_key),
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status == "succeeded":
            logger.info("yookassa_verify_payment: order_id=%s CONFIRMED", order_id)
            return 1
        logger.info("yookassa_verify_payment: order_id=%s status=%s", order_id, status)
        return 0


# ---------------------------------------------------------------------------
# Terminal tools — write PG state
# These are the ONLY places allowed to mutate entity state.
# Each must execute ALL PG writes inside a single transaction.
# External calls FORBIDDEN inside transaction block.
# ---------------------------------------------------------------------------


async def write_order_state_payment_pending(
    session: AsyncSession,
    order_id: str,
    payment_id: str,
    **kwargs: object,
) -> str:
    """Terminal tool: CONFIRMED → PAYMENT_PENDING. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.orders.models import OrderState

    row = await session.execute(
        sql_text("SELECT state FROM orders WHERE id = :id FOR UPDATE"),
        {"id": UUID(order_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_order_state_payment_pending: order %s not found", order_id)
        raise ValueError(f"order not found: {order_id}")
    if current != OrderState.CONFIRMED.value:
        logger.warning(
            "write_order_state_payment_pending: expected CONFIRMED, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected CONFIRMED, got {current}"
        )
    await session.execute(
        sql_text(
            "UPDATE orders SET state = :state, payment_id = :payment_id WHERE id = :id"
        ),
        {
            "id": UUID(order_id),
            "state": OrderState.PAYMENT_PENDING.value,
            "payment_id": payment_id,
        },
    )
    logger.info("write_order_state_payment_pending: order_id=%s", order_id)
    return "OK"


async def write_order_state_paid(session: AsyncSession, order_id: str, **kwargs: object) -> str:
    """Terminal tool: PAYMENT_PENDING → PAID. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.orders.models import OrderState

    row = await session.execute(
        sql_text("SELECT state FROM orders WHERE id = :id FOR UPDATE"),
        {"id": UUID(order_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_order_state_paid: order %s not found", order_id)
        raise ValueError(f"order not found: {order_id}")
    if current != OrderState.PAYMENT_PENDING.value:
        logger.warning(
            "write_order_state_paid: expected PAYMENT_PENDING, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected PAYMENT_PENDING, got {current}"
        )
    await session.execute(
        sql_text("UPDATE orders SET state = :state WHERE id = :id"),
        {"id": UUID(order_id), "state": OrderState.PAID.value},
    )
    logger.info("write_order_state_paid: order_id=%s", order_id)
    return "OK"


async def write_order_state_payment_failed(
    session: AsyncSession, order_id: str, **kwargs: object,
) -> str:
    """Terminal tool: PAYMENT_PENDING → CONFIRMED (rollback). Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.orders.models import OrderState

    row = await session.execute(
        sql_text("SELECT state FROM orders WHERE id = :id FOR UPDATE"),
        {"id": UUID(order_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_order_state_payment_failed: order %s not found", order_id)
        raise ValueError(f"order not found: {order_id}")
    if current != OrderState.PAYMENT_PENDING.value:
        logger.warning(
            "write_order_state_payment_failed: expected PAYMENT_PENDING, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected PAYMENT_PENDING, got {current}"
        )
    await session.execute(
        sql_text("UPDATE orders SET state = :state WHERE id = :id"),
        {"id": UUID(order_id), "state": OrderState.CONFIRMED.value},
    )
    logger.info("write_order_state_payment_failed: order_id=%s", order_id)
    return "OK"


async def write_order_state_cooking(
    session: AsyncSession,
    order_id: str,
    ticket_id: str,
    **kwargs: object,
) -> str:
    """Terminal tool: PAID|CONFIRMED → COOKING. Writes order + ticket in single PG tx.

    Accepts both PAID (card payments) and CONFIRMED (cash payments that skip
    the payment step) as valid prior states.
    """  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.orders.models import OrderState

    _ALLOWED_PRIOR = {OrderState.PAID.value, OrderState.CONFIRMED.value}

    row = await session.execute(
        sql_text("SELECT state FROM orders WHERE id = :id FOR UPDATE"),
        {"id": UUID(order_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_order_state_cooking: order %s not found", order_id)
        raise ValueError(f"order not found: {order_id}")
    if current not in _ALLOWED_PRIOR:
        logger.warning(
            "write_order_state_cooking: expected PAID or CONFIRMED, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected PAID or CONFIRMED, got {current}"
        )
    await session.execute(
        sql_text("UPDATE orders SET state = :state WHERE id = :id"),
        {"id": UUID(order_id), "state": OrderState.COOKING.value},
    )
    await session.execute(
        sql_text(
            "UPDATE kitchen_tickets SET order_id = :order_id WHERE id = :id"
        ),
        {"id": UUID(ticket_id), "order_id": UUID(order_id)},
    )
    logger.info("write_order_state_cooking: order_id=%s ticket_id=%s", order_id, ticket_id)
    return "OK"


# ---------------------------------------------------------------------------
# Inventory (M2+)
# ---------------------------------------------------------------------------


async def reserve_inventory_items(session: AsyncSession, order_id: str, **kwargs: object) -> int:
    """Returns 1 if reserved, 0 if insufficient. Numeric sentinel."""
    from sqlalchemy import text as sql_text

    row = await session.execute(
        sql_text("SELECT items FROM orders WHERE id = :id"),
        {"id": UUID(order_id)},
    )
    row_data = row.scalar_one_or_none()
    if row_data is None:
        logger.warning("reserve_inventory_items: order %s not found", order_id)
        return 0
    items = row_data if isinstance(row_data, list) else json.loads(row_data)
    for item in items:
        sku = item.get("sku") if isinstance(item, dict) else ""
        qty = int(item.get("qty", 1)) if isinstance(item, dict) else 1
        if not sku:
            continue
        stock = await session.execute(
            sql_text("SELECT quantity FROM inventory WHERE sku = :sku FOR UPDATE"),
            {"sku": sku},
        )
        stock_row = stock.scalar_one_or_none()
        if stock_row is None or int(stock_row) < qty:
            logger.warning(
                "reserve_inventory_items: insufficient stock for sku=%s", sku
            )
            return 0
        await session.execute(
            sql_text(
                "UPDATE inventory SET quantity = quantity - :qty WHERE sku = :sku"
            ),
            {"sku": sku, "qty": qty},
        )
    logger.info("reserve_inventory_items: order_id=%s reserved", order_id)
    return 1


async def create_kitchen_ticket(session: AsyncSession, order_id: str, **kwargs: object) -> str:
    """Creates kitchen_ticket record. Returns ticket_id."""
    from sqlalchemy import text as sql_text

    result = await session.execute(
        sql_text(
            "INSERT INTO kitchen_tickets (order_id, state) "
            "VALUES (:order_id, 'NEW') RETURNING id"
        ),
        {"order_id": UUID(order_id)},
    )
    ticket_row = result.one()
    ticket_id = str(ticket_row._mapping["id"])
    logger.info("create_kitchen_ticket: order_id=%s ticket_id=%s", order_id, ticket_id)
    return ticket_id


# ---------------------------------------------------------------------------
# Logging / notification tools
# ---------------------------------------------------------------------------


async def transition_order_state(
    session: AsyncSession,
    order_id: str,
    from_state: str,
    to_state: str,
    **kwargs: object,
) -> str:
    """Generic terminal tool: validates current == from_state, writes to_state.
    Used by simple transition programs (CONFIRM, CANCEL, etc.)."""
    from sqlalchemy import text as sql_text

    row = await session.execute(
        sql_text("SELECT state FROM orders WHERE id = :id FOR UPDATE"),
        {"id": UUID(order_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("transition_order_state: order %s not found", order_id)
        raise ValueError(f"order not found: {order_id}")
    if current != from_state:
        logger.warning(
            "transition_order_state: expected %s, got %s for order %s",
            from_state, current, order_id,
        )
        raise ValueError(
            f"invalid state transition: expected {from_state}, got {current}"
        )
    await session.execute(
        sql_text("UPDATE orders SET state = :state WHERE id = :id"),
        {"id": UUID(order_id), "state": to_state},
    )
    logger.info("transition_order_state: order_id=%s %s → %s", order_id, from_state, to_state)
    return "OK"


async def log_validation_failure(order_id: str, **kwargs: object) -> str:
    logger.warning("log_validation_failure: order_id=%s", order_id)
    return "LOGGED"


async def notify_inventory_insufficient(order_id: str, **kwargs: object) -> str:
    logger.warning("notify_inventory_insufficient: order_id=%s", order_id)
    return "NOTIFIED"
