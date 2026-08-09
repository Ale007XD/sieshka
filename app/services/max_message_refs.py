"""app/services/max_message_refs.py — message_id × recipient tracking.

2026-08-09: enables edit-in-place MAX status cards (see max_staff_notify.py
docstring/DECISIONS.md "chain-notify v1 — БЕЗ message-editing" for the
originally-deferred tradeoff this implements).

Self-contained (own session via app.db.async_session_factory) — same reason
app.services.max_staff_notify._fetch_order_details keeps its own session
rather than importing a service layer: avoids adding a new module-level edge
into the app.services.__init__ eager-import chain for a small, purely
transactional read/write that doesn't need anything from that chain.

Both functions swallow their own exceptions and never raise — a tracking-row
hiccup (DB blip, race) must never block the actual MAX notification, which is
itself already fire-and-forget. Worst case on failure: get_message_ref()
returns None (caller sends a fresh message instead of editing) or
save_message_ref() silently fails to persist the new message_id (caller sends
a fresh message again next time, instead of editing) — degrades gracefully to
the pre-2026-08-09 "always send new" behavior, never to a crash.
"""
from __future__ import annotations

import logging

from sqlalchemy import text as sql_text

from app.db import async_session_factory

logger = logging.getLogger(__name__)


async def get_message_ref(entity_kind: str, entity_id: str, max_user_id: int) -> str | None:
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_text(
                    "SELECT message_id FROM max_message_refs "
                    "WHERE entity_kind = :kind AND entity_id = :id AND max_user_id = :uid"
                ),
                {"kind": entity_kind, "id": entity_id, "uid": max_user_id},
            )
            row = result.fetchone()
    except Exception:
        logger.exception(
            "get_message_ref: query failed kind=%s id=%s uid=%s",
            entity_kind,
            entity_id,
            max_user_id,
        )
        return None
    return row._mapping["message_id"] if row is not None else None


async def save_message_ref(
    entity_kind: str, entity_id: str, max_user_id: int, message_id: str
) -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(
                sql_text(
                    "INSERT INTO max_message_refs "
                    "(entity_kind, entity_id, max_user_id, message_id, updated_at) "
                    "VALUES (:kind, :id, :uid, :mid, now()) "
                    "ON CONFLICT (entity_kind, entity_id, max_user_id) "
                    "DO UPDATE SET message_id = :mid, updated_at = now()"
                ),
                {"kind": entity_kind, "id": entity_id, "uid": max_user_id, "mid": message_id},
            )
            await session.commit()
    except Exception:
        logger.exception(
            "save_message_ref: write failed kind=%s id=%s uid=%s mid=%s",
            entity_kind,
            entity_id,
            max_user_id,
            message_id,
        )
