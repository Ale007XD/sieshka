from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.services.narrative_receipt_service import NarrativeReceipt


class NarrativeReceiptRepository:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def save(self, receipt: NarrativeReceipt) -> str:
        session = self._session or async_session_factory()
        if self._session is None:
            async with session:
                return await self._do_save(session, receipt)
        return await self._do_save(session, receipt)

    async def _do_save(self, session: AsyncSession, receipt: NarrativeReceipt) -> str:
        receipt_id = str(uuid.uuid4())
        await session.execute(
            text(
                "INSERT INTO narrative_receipts (id, decision, reason, rules, trace_ids) "
                "VALUES (:id, :decision, :reason, :rules, :trace_ids)"
            ),
            {
                "id": receipt_id,
                "decision": receipt.decision,
                "reason": receipt.reason,
                "rules": json.dumps(list(receipt.rules)),
                "trace_ids": json.dumps(list(receipt.trace_ids)),
            },
        )
        return receipt_id

    async def find_by_trace_id(self, trace_id: str) -> NarrativeReceipt | None:
        session = self._session or async_session_factory()
        if self._session is None:
            async with session:
                return await self._do_find_by_trace_id(session, trace_id)
        return await self._do_find_by_trace_id(session, trace_id)

    async def _do_find_by_trace_id(
        self, session: AsyncSession, trace_id: str
    ) -> NarrativeReceipt | None:
        result = await session.execute(
            text(
                "SELECT decision, reason, rules, trace_ids "
                "FROM narrative_receipts "
                "WHERE trace_ids @> :trace_id_json "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"trace_id_json": json.dumps([trace_id])},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return NarrativeReceipt(
            decision=row.decision,
            reason=row.reason,
            rules=tuple(json.loads(row.rules)),
            trace_ids=tuple(json.loads(row.trace_ids)),
        )
