"""
app/tools/kitchen_tools.py — nano-vm Tools for kitchen Programs.
M3+: registered with GovernedToolExecutor.

CONSTRAINTS:
  - Terminal tools: single PG transaction, NO external HTTP/MQ calls inside
  - Always read current PG row before writing
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.db import async_session_factory

logger = logging.getLogger(__name__)


async def write_kitchen_state_queued(ticket_id: str, **kwargs: object) -> str:
    """Terminal tool: NEW → QUEUED. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
            {"id": UUID(ticket_id)},
        )
        current = row.scalar_one_or_none()
        if current is None:
            logger.error("write_kitchen_state_queued: ticket %s not found", ticket_id)
            return "ERROR"
        if current != KitchenState.NEW.value:
            logger.warning(
                "write_kitchen_state_queued: expected NEW, got %s", current
            )
            return "ERROR"
        await session.execute(
            sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
            {"id": UUID(ticket_id), "state": KitchenState.QUEUED.value},
        )
        await session.commit()
    logger.info("write_kitchen_state_queued: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_preparing(ticket_id: str, **kwargs: object) -> str:
    """Terminal tool: QUEUED → PREPARING. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
            {"id": UUID(ticket_id)},
        )
        current = row.scalar_one_or_none()
        if current is None:
            logger.error("write_kitchen_state_preparing: ticket %s not found", ticket_id)
            return "ERROR"
        if current != KitchenState.QUEUED.value:
            logger.warning(
                "write_kitchen_state_preparing: expected QUEUED, got %s", current
            )
            return "ERROR"
        await session.execute(
            sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
            {"id": UUID(ticket_id), "state": KitchenState.PREPARING.value},
        )
        await session.commit()
    logger.info("write_kitchen_state_preparing: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_ready(ticket_id: str, **kwargs: object) -> str:
    """Terminal tool: PREPARING → READY. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
            {"id": UUID(ticket_id)},
        )
        current = row.scalar_one_or_none()
        if current is None:
            logger.error("write_kitchen_state_ready: ticket %s not found", ticket_id)
            return "ERROR"
        if current != KitchenState.PREPARING.value:
            logger.warning(
                "write_kitchen_state_ready: expected PREPARING, got %s", current
            )
            return "ERROR"
        await session.execute(
            sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
            {"id": UUID(ticket_id), "state": KitchenState.READY.value},
        )
        await session.commit()
    logger.info("write_kitchen_state_ready: ticket_id=%s", ticket_id)
    return "OK"


async def write_kitchen_state_handed_off(ticket_id: str, **kwargs: object) -> str:
    """Terminal tool: READY → HANDED_OFF. Atomic PG write."""  # terminal-tool
    from sqlalchemy import text as sql_text

    from app.domains.kitchen.fsm import KitchenState

    async with async_session_factory() as session:
        row = await session.execute(
            sql_text("SELECT state FROM kitchen_tickets WHERE id = :id FOR UPDATE"),
            {"id": UUID(ticket_id)},
        )
        current = row.scalar_one_or_none()
        if current is None:
            logger.error("write_kitchen_state_handed_off: ticket %s not found", ticket_id)
            return "ERROR"
        if current != KitchenState.READY.value:
            logger.warning(
                "write_kitchen_state_handed_off: expected READY, got %s", current
            )
            return "ERROR"
        await session.execute(
            sql_text("UPDATE kitchen_tickets SET state = :state WHERE id = :id"),
            {"id": UUID(ticket_id), "state": KitchenState.HANDED_OFF.value},
        )
        await session.commit()
    logger.info("write_kitchen_state_handed_off: ticket_id=%s", ticket_id)
    return "OK"
