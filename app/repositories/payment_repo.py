"""app/repositories/payment_repo.py — PaymentRepository for payments + idempotency_keys."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        provider_id: str,
        state: str = "PENDING",
        raw_response: dict[str, object] | None = None,
    ) -> str:
        result = await self._session.execute(
            text(
                "INSERT INTO payments "
                "(order_id, provider, provider_id, amount, currency, state, raw_response) "
                "VALUES (:order_id, 'yookassa', :provider_id, :amount, "
                ":currency, :state, :raw_response) RETURNING id"
            ),
            {
                "order_id": UUID(order_id),
                "provider_id": provider_id,
                "amount": str(amount),
                "currency": currency,
                "state": state,
                "raw_response": json.dumps(raw_response) if raw_response else None,
            },
        )
        row = result.one()
        payment_id = str(row._mapping["id"])
        logger.info("PaymentRepository: created payment %s for order %s", payment_id, order_id)
        return payment_id

    async def get_by_provider_id(self, provider_id: str) -> dict[str, object]:
        result = await self._session.execute(
            text(
                "SELECT id, order_id, state, amount, currency FROM payments "
                "WHERE provider_id = :provider_id"
            ),
            {"provider_id": provider_id},
        )
        row = result.one()
        return {
            "id": str(row._mapping["id"]),
            "order_id": str(row._mapping["order_id"]),
            "state": str(row._mapping["state"]),
            "amount": row._mapping["amount"],
            "currency": str(row._mapping["currency"]),
        }

    async def write_state(self, entity_id: str, state: str) -> None:
        await self._session.execute(
            text("UPDATE payments SET state = :state WHERE id = :id"),
            {"id": UUID(entity_id), "state": state},
        )
        logger.info("PaymentRepository: wrote state %s for payment %s", state, entity_id)

    async def get_state(self, entity_id: str) -> str:
        result = await self._session.execute(
            text("SELECT state FROM payments WHERE id = :id"),
            {"id": UUID(entity_id)},
        )
        row = result.scalar_one()
        return str(row)
