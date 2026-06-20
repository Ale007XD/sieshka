"""
app/tools/order_tools.py — nano-vm Tools for order Programs.
M3+: registered with GovernedToolExecutor.

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for CONDITION steps — not string literals
  - PG transaction: read + write inside single transaction in terminal tools
  - External calls (HTTP, MQ) FORBIDDEN inside PG transaction block
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def validate_order_items(order_id: str, **kwargs: object) -> int:
    """Returns 1 if valid, 0 if invalid. Numeric sentinel for ASTEngine."""
    # TODO M2: query PG orders + menu items
    logger.info("validate_order_items: order_id=%s", order_id)
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
    # TODO M2: call YooKassa API
    # Include trace_id in metadata from kwargs["trace_id"]
    logger.info("yookassa_create_payment: order_id=%s amount=%s", order_id, amount)
    return "payment_placeholder_id"


async def yookassa_verify_payment(
    order_id: str,
    payment_id: str,
    **kwargs: object,
) -> int:
    """Returns 1 if confirmed, 0 if failed. Numeric sentinel."""
    logger.info("yookassa_verify_payment: order_id=%s payment_id=%s", order_id, payment_id)
    return 1


# ---------------------------------------------------------------------------
# Terminal tools — write PG state
# These are the ONLY places allowed to mutate entity state.
# Each must execute ALL PG writes inside a single transaction.
# External calls FORBIDDEN inside transaction block.
# ---------------------------------------------------------------------------


async def write_order_state_payment_pending(
    order_id: str,
    payment_id: str,
    **kwargs: object,
) -> str:
    """Terminal tool: CONFIRMED → PAYMENT_PENDING. Atomic PG write."""  # terminal-tool
    # TODO M2: db.execute("UPDATE orders SET state='PAYMENT_PENDING', payment_id=... WHERE id=...")
    logger.info("write_order_state_payment_pending: order_id=%s", order_id)
    return "OK"


async def write_order_state_paid(order_id: str, **kwargs: object) -> str:
    """Terminal tool: PAYMENT_PENDING → PAID. Atomic PG write."""  # terminal-tool
    logger.info("write_order_state_paid: order_id=%s", order_id)
    return "OK"


async def write_order_state_payment_failed(order_id: str, **kwargs: object) -> str:
    """Terminal tool: PAYMENT_PENDING → CONFIRMED (rollback). Atomic PG write."""  # terminal-tool
    logger.info("write_order_state_payment_failed: order_id=%s", order_id)
    return "OK"


async def write_order_state_cooking(
    order_id: str,
    ticket_id: str,
    **kwargs: object,
) -> str:
    """Terminal tool: PAID → COOKING. Writes order + ticket in single PG tx."""  # terminal-tool
    logger.info("write_order_state_cooking: order_id=%s ticket_id=%s", order_id, ticket_id)
    return "OK"


# ---------------------------------------------------------------------------
# Inventory (M2+)
# ---------------------------------------------------------------------------


async def reserve_inventory_items(order_id: str, **kwargs: object) -> int:
    """Returns 1 if reserved, 0 if insufficient. Numeric sentinel."""
    logger.info("reserve_inventory_items: order_id=%s", order_id)
    return 1


async def create_kitchen_ticket(order_id: str, **kwargs: object) -> str:
    """Creates kitchen_ticket record. Returns ticket_id."""
    logger.info("create_kitchen_ticket: order_id=%s", order_id)
    return "ticket_placeholder_id"


# ---------------------------------------------------------------------------
# Logging / notification tools
# ---------------------------------------------------------------------------


async def log_validation_failure(order_id: str, **kwargs: object) -> str:
    logger.warning("log_validation_failure: order_id=%s", order_id)
    return "LOGGED"


async def notify_inventory_insufficient(order_id: str, **kwargs: object) -> str:
    logger.warning("notify_inventory_insufficient: order_id=%s", order_id)
    return "NOTIFIED"
