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
from app.services.payment_service import (
    PaymentService,
    YooKassaClient,
    build_yookassa_receipt,
)


class TestBuildYookassaReceipt:
    def test_single_line_matches_amount_exactly(self) -> None:
        receipt = build_yookassa_receipt(
            phone="+79991234567",
            amount=decimal.Decimal("2350.50"),
            description="Order abc-123",
        )
        assert receipt["customer"] == {"phone": "+79991234567"}
        assert len(receipt["items"]) == 1
        item = receipt["items"][0]
        assert item["amount"] == {"value": "2350.50", "currency": "RUB"}
        assert item["quantity"] == "1"
        assert item["vat_code"] == 1
        assert item["payment_mode"] == "full_prepayment"
        assert item["payment_subject"] == "commodity"

    def test_description_truncated_to_128_chars(self) -> None:
        receipt = build_yookassa_receipt(
            phone="+79991234567",
            amount=decimal.Decimal("100.00"),
            description="x" * 200,
        )
        assert len(receipt["items"][0]["description"]) == 128


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

        # Tool transition_order_state reads current state from the same session
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.CONFIRMED.value

        session.execute.side_effect = [
            mock_insert_result,  # PaymentRepository.create INSERT
            mock_tool_select,    # tool SELECT ... FOR UPDATE
            MagicMock(),          # tool UPDATE (return value unused)
        ]

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

        with (
            patch("app.services.payment_service.trace.record", return_value=trace_id_val),
            patch.object(PaymentRepository, "create", AsyncMock(return_value=str(uuid4()))),
            patch.object(OrderRepository, "get_state", return_value=OrderState.CONFIRMED),
        ):
            result = await svc.create_payment(
                order_id=order_id,
                amount=amount,
                currency="RUB",
                description="Test order",
            )

        assert result["confirmation_url"] == confirmation_url
        assert result["payment_id"] == provider_id
        assert result["trace_id"] == trace_id_val
        # No customer_phone passed → no receipt attached (legacy/back-compat
        # call sites keep working exactly as before this patch).
        assert yookassa_mock.create_payment.call_args.kwargs["receipt"] is None
        # sprint_yookassa_manual_integration: no payment_method_data passed
        # by the caller here -> passed through as None to YooKassaClient
        # (the embedded/no-restriction confirmation.type="embedded" default
        # path — still used by the legacy /orders/{id}/pay endpoint, not by
        # checkout.py's cart.js contract anymore).
        assert yookassa_mock.create_payment.call_args.kwargs["payment_method_data"] is None

    async def test_create_payment_forwards_payment_method_data(self) -> None:
        """sprint_yookassa_manual_integration (2026-08-19): checkout.py sets
        payment_method_data={"type": "sbp"|"sberbank"} explicitly — this is
        what actually reaches YooKassa's manual-integration API (unlike the
        old payment_method_types field, which never controlled what the
        now-removed embedded widget displayed to begin with)."""
        order_id = str(uuid4())
        amount = decimal.Decimal("500.00")
        session = AsyncMock()
        mock_insert_result = MagicMock()
        mock_insert_result.one.return_value = MagicMock(_mapping={"id": uuid4()})
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.CONFIRMED.value
        session.execute = AsyncMock(
            side_effect=[mock_insert_result, mock_tool_select, MagicMock()]
        )

        yookassa_mock = MagicMock(spec=YooKassaClient)
        yookassa_mock.create_payment = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yookassa.ru/redirect"},
            }
        )
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=yookassa_mock,
        )

        with (
            patch("app.services.payment_service.trace.record", return_value="trace-id"),
            patch.object(PaymentRepository, "create", AsyncMock(return_value=str(uuid4()))),
            patch.object(OrderRepository, "get_state", return_value=OrderState.CONFIRMED),
        ):
            await svc.create_payment(
                order_id=order_id,
                amount=amount,
                confirmation_type="redirect",
                payment_method_data={"type": "sbp"},
            )

        assert yookassa_mock.create_payment.call_args.kwargs["payment_method_data"] == {
            "type": "sbp"
        }

    async def test_create_payment_rejects_unknown_payment_method_data_type(self) -> None:
        """Defense-in-depth: a typo/unexpected method type here would
        otherwise surface as an opaque YooKassa 400 deep inside httpx."""
        order_id = str(uuid4())
        amount = decimal.Decimal("500.00")
        session = AsyncMock()
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=MagicMock(spec=YooKassaClient),
        )

        with (
            patch("app.services.payment_service.trace.record", return_value="trace-id"),
            pytest.raises(ValueError, match="payment_method_data type must be one of"),
        ):
            await svc.create_payment(
                order_id=order_id,
                amount=amount,
                confirmation_type="redirect",
                payment_method_data={"type": "bank_card"},
            )

    async def test_create_payment_attaches_receipt_when_phone_given(self) -> None:
        """sprint_yookassa_receipt_54fz: live YooKassa shops with an
        online-kassa/54-FZ connection reject create_payment without a
        receipt object (confirmed root cause of the 502s, see
        SieshKa-Site's app/payments.py::_build_receipt for the working
        reference implementation this mirrors)."""
        order_id = str(uuid4())
        amount = decimal.Decimal("1500.00")
        provider_id = str(uuid4())

        session = AsyncMock()
        mock_insert_result = MagicMock()
        mock_insert_result.one.return_value = MagicMock(_mapping={"id": uuid4()})
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.CONFIRMED.value
        session.execute = AsyncMock(
            side_effect=[mock_insert_result, mock_tool_select, MagicMock()]
        )

        yookassa_mock = MagicMock(spec=YooKassaClient)
        yookassa_mock.create_payment = AsyncMock(
            return_value={
                "id": provider_id,
                "status": "pending",
                "confirmation": {"confirmation_token": "tok"},
            }
        )
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=yookassa_mock,
        )

        with (
            patch("app.services.payment_service.trace.record", return_value="trace-id"),
            patch.object(PaymentRepository, "create", AsyncMock(return_value=str(uuid4()))),
            patch.object(OrderRepository, "get_state", return_value=OrderState.CONFIRMED),
        ):
            await svc.create_payment(
                order_id=order_id,
                amount=amount,
                currency="RUB",
                description="Order test",
                customer_phone="+79991234567",
            )

        receipt = yookassa_mock.create_payment.call_args.kwargs["receipt"]
        assert receipt is not None
        assert receipt["customer"]["phone"] == "+79991234567"
        assert receipt["items"][0]["amount"]["value"] == "1500.00"
        # Sum of receipt items must exactly equal the payment amount — this
        # is a hard YooKassa API constraint, single-line construction
        # guarantees it by design regardless of promo/delivery-fee splits.
        assert receipt["items"][0]["vat_code"] == 1

    async def test_create_payment_forwards_confirmation_type(self) -> None:
        """sprint_telegram_3ds_webview_redirect: confirmation_type must reach
        YooKassaClient.create_payment unchanged — checkout.py decides
        embedded-vs-redirect based on X-Telegram-Init-Data presence,
        PaymentService is a pure pass-through here, not a policy layer."""
        order_id = str(uuid4())
        amount = decimal.Decimal("1500.00")

        session = AsyncMock()
        mock_insert_result = MagicMock()
        mock_insert_result.one.return_value = MagicMock(_mapping={"id": uuid4()})
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.CONFIRMED.value
        session.execute = AsyncMock(
            side_effect=[mock_insert_result, mock_tool_select, MagicMock()]
        )

        yookassa_mock = MagicMock(spec=YooKassaClient)
        yookassa_mock.create_payment = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yookassa.ru/redirect"},
            }
        )
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=yookassa_mock,
        )

        with (
            patch("app.services.payment_service.trace.record", return_value="trace-id"),
            patch.object(PaymentRepository, "create", AsyncMock(return_value=str(uuid4()))),
            patch.object(OrderRepository, "get_state", return_value=OrderState.CONFIRMED),
        ):
            await svc.create_payment(
                order_id=order_id,
                amount=amount,
                currency="RUB",
                description="Order test",
                confirmation_type="redirect",
            )

        assert yookassa_mock.create_payment.call_args.kwargs["confirmation_type"] == "redirect"

    async def test_confirm_payment_success(self) -> None:
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        # Tool transition_order_state reads/writes via the shared session
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.PAYMENT_PENDING.value
        session.execute.return_value = mock_tool_select

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
        ):
            result = await svc.confirm_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.PAID

    async def test_confirm_payment_notifies_staff_with_paid_state(self) -> None:
        """sprint_fix_online_payment_funnel (2026-08-19): staff notification
        moved here from checkout.py (payment-link-creation time) — it must
        fire only after the payment is genuinely confirmed, with the order's
        REAL resulting state. OrderEvent.PAYMENT_CONFIRMED transitions to
        OrderState.PAID (ORDER_TRANSITIONS), not to a same-named
        OrderState.PAYMENT_CONFIRMED member — that enum value exists but the
        order never actually lands on it here; asserting the exact state
        catches that mixup directly."""
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.PAYMENT_PENDING.value
        session.execute.return_value = mock_tool_select

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
            patch(
                "app.services.payment_service.notify_admin_order_state", AsyncMock(),
            ) as mock_admin,
            patch(
                "app.services.payment_service.notify_staff_card", AsyncMock(),
            ) as mock_staff,
            patch(
                "app.services.order_service.OrderService.transition_order",
                AsyncMock(return_value=TransitionResult(
                    success=True, new_state=OrderState.COOKING,
                    rejected_event=None, reason=None,
                )),
            ),
        ):
            await svc.confirm_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        mock_admin.assert_awaited_once_with(order_id, OrderState.PAID)
        mock_staff.assert_awaited_once_with(order_id, order_state=OrderState.PAID)

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

    async def test_fail_payment_success(self) -> None:
        """payment.canceled path: PAYMENT_PENDING -> CONFIRMED via
        OrderEvent.PAYMENT_FAILED (ORDER_TRANSITIONS), payment row -> FAILED.
        Regression for the webhook gap found 2026-08-20 (order stuck at
        PAYMENT_PENDING forever — only payment.succeeded was handled)."""
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.PAYMENT_PENDING.value
        session.execute.return_value = mock_tool_select

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
            ) ,
            patch.object(PaymentRepository, "write_state", AsyncMock()) as mock_write,
            patch.object(OrderRepository, "get_state", return_value=OrderState.PAYMENT_PENDING),
        ):
            result = await svc.fail_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.CONFIRMED
        mock_write.assert_awaited_once()
        assert mock_write.await_args.args[1] == "FAILED"

    async def test_fail_payment_does_not_notify_staff(self) -> None:
        """The kitchen never saw this order (never reached PAID) — a
        canceled payment must not trigger notify_admin_order_state or
        notify_staff_card."""
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        mock_tool_select = MagicMock()
        mock_tool_select.scalar_one_or_none.return_value = OrderState.PAYMENT_PENDING.value
        session.execute.return_value = mock_tool_select

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
            patch(
                "app.services.payment_service.notify_admin_order_state", AsyncMock(),
            ) as mock_admin,
            patch(
                "app.services.payment_service.notify_staff_card", AsyncMock(),
            ) as mock_staff,
        ):
            await svc.fail_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        mock_admin.assert_not_awaited()
        mock_staff.assert_not_awaited()

    async def test_fail_payment_duplicate(self) -> None:
        order_id = str(uuid4())
        payment_id = str(uuid4())
        trace_id_val = str(uuid4())

        session = AsyncMock()
        svc = PaymentService(
            session_factory=lambda: _session_factory(session),  # type: ignore[arg-type]
            yookassa=MagicMock(spec=YooKassaClient),
        )

        with patch.object(IdempotencyService, "check_and_record", return_value=False):
            result = await svc.fail_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.reason == "Duplicate webhook event"

    async def test_fail_payment_already_terminal(self) -> None:
        """A payment already SUCCESS or FAILED must not be re-processed —
        e.g. a late/duplicate payment.canceled arriving after
        payment.succeeded already flipped the payment to SUCCESS."""
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
            result = await svc.fail_payment(
                order_id=order_id,
                provider_id=payment_id,
                trace_id=trace_id_val,
            )

        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.reason == "Payment already SUCCESS"

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
