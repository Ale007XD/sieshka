"""
app/tools/kitchen_tools.py — nano-vm Tools for kitchen Programs.
M3+: registered with GovernedToolExecutor.
Session injected via functools.partial at VM-construction time
(not through **kwargs — Trace/Receipt contract must stay JSON-serializable).

CONSTRAINTS:
  - Terminal tools: single PG transaction, NO external HTTP/MQ calls inside
  - Always read current PG row before writing
  - session is a named first parameter (not **kwargs) — closure-injected by
    functools.partial in _build_vm(), never serialised in Trace/Receipt
  - Terminal TOOL step failure propagation: no downstream CONDITION reads
    these outputs — raise on error, never return an ERROR sentinel string
    (CONSTRAINTS.md 2026-07-02)
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def write_kitchen_state_queued(
    session: AsyncSession, ticket_id: str, **kwargs: object
) -> str:
    """Terminal tool: NEW → QUEUED. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    row = await session.execute(
        sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
        {"id": UUID(ticket_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_kitchen_state_queued: ticket %s not found", ticket_id)
        raise ValueError(f"ticket not found: {ticket_id}")
    if current != KitchenState.NEW.value:
        logger.warning(
            "write_kitchen_state_queued: expected NEW, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected NEW, got {current}"
        )
    await session.execute(
        sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
        {"id": UUID(ticket_id), "state": KitchenState.QUEUED.value},
    )
    logger.info("write_kitchen_state_queued: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_preparing(
    session: AsyncSession, ticket_id: str, **kwargs: object
) -> str:
    """Terminal tool: QUEUED → PREPARING. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    row = await session.execute(
        sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
        {"id": UUID(ticket_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_kitchen_state_preparing: ticket %s not found", ticket_id)
        raise ValueError(f"ticket not found: {ticket_id}")
    if current != KitchenState.QUEUED.value:
        logger.warning(
            "write_kitchen_state_preparing: expected QUEUED, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected QUEUED, got {current}"
        )
    await session.execute(
        sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
        {"id": UUID(ticket_id), "state": KitchenState.PREPARING.value},
    )
    logger.info("write_kitchen_state_preparing: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_ready(
    session: AsyncSession, ticket_id: str, **kwargs: object
) -> str:
    """Terminal tool: PREPARING → READY. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    row = await session.execute(
        sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
        {"id": UUID(ticket_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_kitchen_state_ready: ticket %s not found", ticket_id)
        raise ValueError(f"ticket not found: {ticket_id}")
    if current != KitchenState.PREPARING.value:
        logger.warning(
            "write_kitchen_state_ready: expected PREPARING, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected PREPARING, got {current}"
        )
    await session.execute(
        sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
        {"id": UUID(ticket_id), "state": KitchenState.READY.value},
    )
    logger.info("write_kitchen_state_ready: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_handed_off(
    session: AsyncSession, ticket_id: str, **kwargs: object
) -> str:
    """Terminal tool: READY → HANDED_OFF. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    row = await session.execute(
        sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
        {"id": UUID(ticket_id)},
    )
    current = row.scalar_one_or_none()
    if current is None:
        logger.error("write_kitchen_state_handed_off: ticket %s not found", ticket_id)
        raise ValueError(f"ticket not found: {ticket_id}")
    if current != KitchenState.READY.value:
        logger.warning(
            "write_kitchen_state_handed_off: expected READY, got %s", current
        )
        raise ValueError(
            f"invalid state transition: expected READY, got {current}"
        )
    await session.execute(
        sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
        {"id": UUID(ticket_id), "state": KitchenState.HANDED_OFF.value},
    )
    logger.info("write_kitchen_state_handed_off: ticket_id=%s", ticket_id)
    return "OK"
