"""app/services/idempotency.py — IdempotencyService using idempotency_keys table.

M2: trace_id + program_step granularity per ARCHITECTURE.md.
Schema mirrors nano-vm-mcp idempotency_keys for M3 migration ease.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory

logger = logging.getLogger(__name__)


class IdempotencyService:
    """Idempotency guard for async resume points (webhooks, scheduled tasks).

    Key format: ``{trace_id}:{program_step}`` — trace_id + program_step granularity.
    Thread-safe via INSERT ... ON CONFLICT DO NOTHING.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def check_and_record(
        self,
        key: str,
        payload: dict[str, object] | None = None,
    ) -> bool:
        """Atomically insert *key* into idempotency_keys.

        Returns ``True`` if this is the first call (key inserted),
        ``False`` if the key already exists (duplicate).
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO idempotency_keys (key, payload) "
                    "VALUES (:key, CAST(:payload AS jsonb)) "
                    "ON CONFLICT DO NOTHING RETURNING key"
                ),
                {"key": key, "payload": json.dumps(payload) if payload else "{}"},
            )
            inserted = result.scalar_one_or_none() is not None
            await session.commit()
            return inserted
