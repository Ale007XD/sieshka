"""app/services/payment_service.py — YooKassa SDK wrapper + payment orchestration."""
from __future__ import annotations

import decimal
import logging
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import async_session_factory
from app.domains.orders.fsm import OrderFSM
from app.domains.orders.models import OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository
from app.repositories.payment_repo import PaymentRepository
from app.trace import trace

logger = logging.getLogger(__name__)


class YooKassaClient:
    """Lightweight YooKassa HTTP client wrapping the REST API."""

    BASE_URL = "https://api.yookassa.ru/v3"

    def __init__(self, shop_id: str, secret_key: str) -> None:
        self._auth = httpx.BasicAuth(shop_id, secret_key)

    async def create_payment(
        self,
        amount: decimal.Decimal,
        currency: str,
        description: str,
        return_url: str,
        metadata: dict[str, str],
    ) -> dict[str, object]:
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description,
            "metadata": metadata,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}/payments",
                auth=self._auth,
                json=payload,
                headers={"Idempotence-Key": str(uuid4())},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]


class PaymentService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        yookassa: YooKassaClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._yookassa = yookassa or YooKassaClient(
            shop_id=settings.YOOKASSA_SHOP_ID,
            secret_key=settings.YOOKASSA_SECRET_KEY,
        )

    async def create_payment(
        self,
        order_id: str,
        amount: decimal.Decimal,
        currency: str = "RUB",
        description: str | None = None,
        return_url: str | None = None,
    ) -> dict[str, object]:
        trace_id = trace.record(
            entity_id=order_id,
            domain="orders",
            event="PAYMENT_REQUESTED",
            from_state=OrderState.CONFIRMED.value,
            to_state=OrderState.PAYMENT_PENDING.value,
            metadata={"amount": str(amount), "currency": currency},
        )

        return_url = return_url or settings.YOOKASSA_RETURN_URL
        desc = description or f"Order {order_id}"

        data = await self._yookassa.create_payment(
            amount=amount,
            currency=currency,
            description=desc,
            return_url=return_url,
            metadata={
                "trace_id": trace_id,
                "order_id": order_id,
                "program_name": "payment_confirmation",
            },
        )

        raw_data: dict[str, object] = data
        provider_id = str(raw_data["id"])
        confirmation_obj = raw_data.get("confirmation", {})
        confirmation_url = ""
        if isinstance(confirmation_obj, dict):
            confirmation_url = str(confirmation_obj.get("confirmation_url", ""))

        async with self._session_factory() as session:
            repo = PaymentRepository(session)
            await repo.create(
                order_id=order_id,
                amount=amount,
                currency=currency,
                provider_id=provider_id,
                state="PENDING",
                raw_response=data,
            )

            order_repo = OrderRepository(session)
            fsm = OrderFSM(
                state_reader=order_repo.get_state,
                state_writer=order_repo.write_state,
            )
            result = await fsm.handle_event(order_id, OrderEvent.REQUEST_PAYMENT)
            await session.commit()

        if not result.success:
            logger.warning(
                "PaymentService: order %s transition to PAYMENT_PENDING failed: %s",
                order_id,
                result.reason,
            )

        return {
            "confirmation_url": confirmation_url,
            "payment_id": provider_id,
            "trace_id": trace_id,
        }

    async def confirm_payment(
        self,
        order_id: str,
        provider_id: str,
        event_id: str,
    ) -> TransitionResult:
        async with self._session_factory() as session:
            repo = PaymentRepository(session)

            if await repo.idempotency_key_exists(event_id):
                logger.info("PaymentService: duplicate webhook event %s — skipping", event_id)
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=None,
                    reason="Duplicate webhook event",
                )

            inserted = await repo.try_set_idempotency_key(
                event_id,
                {"provider_id": provider_id, "order_id": order_id},
            )
            if not inserted:
                logger.info("PaymentService: concurrent webhook event %s — skipping", event_id)
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=None,
                    reason="Concurrent webhook event",
                )

            try:
                payment = await repo.get_by_provider_id(provider_id)
            except Exception:
                logger.warning("PaymentService: payment %s not found — skipping", provider_id)
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=None,
                    reason="Payment not found",
                )
            payment_id = str(payment["id"])
            payment_state = str(payment["state"])
            if payment_state == "SUCCESS":
                logger.info("PaymentService: payment %s already confirmed — skipping", provider_id)
                return TransitionResult(
                    success=False,
                    new_state=None,
                    rejected_event=None,
                    reason="Payment already confirmed",
                )

            await repo.write_state(payment_id, "SUCCESS")

            order_repo = OrderRepository(session)
            fsm = OrderFSM(
                state_reader=order_repo.get_state,
                state_writer=order_repo.write_state,
            )
            result = await fsm.handle_event(order_id, OrderEvent.PAYMENT_CONFIRMED)
            await session.commit()
            return result
