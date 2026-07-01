"""app/services/payment_service.py — YooKassa SDK wrapper + payment orchestration."""
from __future__ import annotations

import decimal
import logging
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

import httpx
from nano_vm.models import Program, Trace, TraceStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import async_session_factory
from app.domains.orders.models import ORDER_TRANSITIONS, OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.idempotency import IdempotencyService
from app.trace import trace

logger = logging.getLogger(__name__)


class _VMProtocol(Protocol):
    """Minimal protocol for ExecutionVM duck-typing."""
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


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
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._yookassa = yookassa or YooKassaClient(
            shop_id=settings.YOOKASSA_SHOP_ID,
            secret_key=settings.YOOKASSA_SECRET_KEY,
        )
        self._idempotency = idempotency or IdempotencyService(session_factory=session_factory)
        self._vm: _VMProtocol | None = None

    def _get_vm(self) -> _VMProtocol:
        if self._vm is None:
            from app.services.order_service import _build_vm

            self._vm = _build_vm()
        return self._vm

    async def _run_simple_transition(
        self,
        order_id: str,
        event: OrderEvent,
        current_state: OrderState,
    ) -> TransitionResult:
        """Execute a simple state transition via ExecutionVM.
        
        Used instead of OrderFSM.handle_event() — simple state write programs
        (no YooKassa or other external logic duplicated from the service layer).
        """
        from nano_vm.models import Program, Step, StepType

        allowed = ORDER_TRANSITIONS.get(current_state, {})
        if event not in allowed:
            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=event,
                reason=f"Event {event!r} not allowed from state {current_state!r}",
            )
        new_state = allowed[event]

        program = Program(
            name=f"order_{event.value.lower()}",
            steps=[
                Step(
                    id="write_state",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": current_state.value,
                        "to_state": new_state.value,
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace_result = await self._get_vm().run(program, context={"order_id": order_id})

        if trace_result.status == TraceStatus.SUCCESS:
            return TransitionResult(
                success=True,
                new_state=new_state,
                rejected_event=None,
                reason=None,
            )
        return TransitionResult(
            success=False,
            new_state=None,
            rejected_event=event,
            reason=trace_result.error or "Execution failed",
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

            current_state = await OrderRepository(session).get_state(order_id)
            result = await self._run_simple_transition(
                order_id, OrderEvent.REQUEST_PAYMENT, current_state,
            )
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
        trace_id: str,
    ) -> TransitionResult:
        idem_key = f"{trace_id}:payment_confirmation"
        inserted = await self._idempotency.check_and_record(
            idem_key,
            {"provider_id": provider_id, "order_id": order_id},
        )
        if not inserted:
            logger.info("PaymentService: duplicate webhook trace_id=%s — skipping", trace_id)
            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=None,
                reason="Duplicate webhook event",
            )

        async with self._session_factory() as session:
            repo = PaymentRepository(session)

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

            current_state = await OrderRepository(session).get_state(order_id)
            result = await self._run_simple_transition(
                order_id, OrderEvent.PAYMENT_CONFIRMED, current_state,
            )
            await session.commit()
            return result
