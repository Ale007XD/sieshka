"""tests/unit/test_payment_service.py — mocked YooKassa client + repo."""
from __future__ import annotations

import decimal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.domains.orders.models import OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.idempotency import IdempotencyService
from app.services.payment_service import PaymentService, YooKassaClient


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestPaymentService:
    @pytest.fixture
    def service(self) -> PaymentService:
        yookassa = MagicMock(spec=YooKassaClient)
        svc = PaymentService(
            session_factory=_session_factory,  # type: ignore[arg-type]
            yookassa=yookassa,
        )
        return svc

    async def test_create_payment_success(self) -> None:
        order_id = str(uuid4())
        amount = decimal.Decimal("1500.00")
        confirmation_url = "https://yoomoney.ru/confirmation/payment_id"
        provider_id = str(uuid4())
        trace_id_val = "test-trace-id"

        session = AsyncMock()
        session.execute = AsyncMock()
        mock_insert_result = MagicMock()
        mock_insert_result.one.return_value = MagicMock(_mapping={"id": uuid4()})

        mock_state_result = MagicMock()
        mock_state_result.scalar_one.return_value = OrderState.CONFIRMED.value

        session.execute.side_effect = [mock_insert_result, mock_state_result]

        yookassa_mock = MagicMock(spec=YooKassaClient)
        yookassa_mock.create_payment = AsyncMock(
            return_value={
                "id": provider_id,
                "status": "pending",
                "confirmation": {"confirmation_url": confirmation_url},
            }
        )
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=yookassa_mock,
        )

        with patch("app.services.payment_service.trace.record", return_value=trace_id_val):
            with patch.object(PaymentRepository, "create", AsyncMock(return_value=str(uuid4()))):
                with patch.object(OrderRepository, "get_state", return_value=OrderState.CONFIRMED):
                    with patch.object(OrderRepository, "write_state", AsyncMock()):
                        result = await svc.create_payment(
                            order_id=order_id,
                            amount=amount,
                            currency="RUB",
                            description="Test order",
                        )

        assert result["confirmation_url"] == confirmation_url
        assert result["payment_id"] == provider_id
        assert result["trace_id"] == trace_id_val

    async def test_confirm_payment_success(self) -> None:
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()

        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=MagicMock(spec=YooKassaClient),
        )

        with (
            patch.object(IdempotencyService, "check_and_record", return_value=True),
            patch.object(
                PaymentRepository,
                "get_by_provider_id",
                return_value={
                    "id": str(uuid4()),
                    "order_id": order_id,
                    "state": "PENDING",
                    "amount": decimal.Decimal("1500.00"),
                    "currency": "RUB",
                },
            ),
            patch.object(PaymentRepository, "write_state", AsyncMock()),
            patch.object(OrderRepository, "get_state", return_value=OrderState.PAYMENT_PENDING),
            patch.object(OrderRepository, "write_state", AsyncMock()),
        ):
            result = await svc.confirm_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.PAID

    async def test_confirm_payment_duplicate(self) -> None:
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=MagicMock(spec=YooKassaClient),
        )

        with patch.object(IdempotencyService, "check_and_record", return_value=False):
            result = await svc.confirm_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.reason == "Duplicate webhook event"

    async def test_confirm_payment_already_paid(self) -> None:
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=MagicMock(spec=YooKassaClient),
        )

        with (
            patch.object(IdempotencyService, "check_and_record", return_value=True),
            patch.object(
                PaymentRepository,
                "get_by_provider_id",
                return_value={
                    "id": str(uuid4()),
                    "order_id": order_id,
                    "state": "SUCCESS",
                    "amount": decimal.Decimal("1500.00"),
                    "currency": "RUB",
                },
            ),
        ):
            result = await svc.confirm_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.reason == "Payment already confirmed"

    async def test_create_payment_yookassa_api_error(self) -> None:
        order_id = str(uuid4())
        amount = decimal.Decimal("500.00")

        session = AsyncMock()

        yookassa_mock = MagicMock(spec=YooKassaClient)
        yookassa_mock.create_payment = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "API error",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )
        )
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=yookassa_mock,
        )

        with patch("app.services.payment_service.trace.record", return_value="trace-id"):
            with pytest.raises(httpx.HTTPStatusError):
                await svc.create_payment(order_id=order_id, amount=amount)
