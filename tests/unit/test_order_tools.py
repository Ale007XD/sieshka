"""tests/unit/test_order_tools.py — MockLLMAdapter pattern, no real API in CI."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.tools.order_tools import (
    create_kitchen_ticket,
    log_validation_failure,
    notify_inventory_insufficient,
    reserve_inventory_items,
    validate_order_items,
    write_order_state_cooking,
    write_order_state_paid,
    write_order_state_payment_failed,
    write_order_state_payment_pending,
    yookassa_create_payment,
    yookassa_verify_payment,
)


@pytest.fixture(autouse=True)
def _mock_session_factory():
    """Mock async_session_factory for all tests — no real PG."""
    with patch("app.tools.order_tools.async_session_factory") as mock:
        yield mock


@pytest.fixture
def mock_session():
    session = AsyncMock()
    # execute() returns CursorResult (sync), not async
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.one.return_value = None
    mock_result.fetchall.return_value = []
    session.execute.return_value = mock_result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestValidateOrderItems:
    async def test_valid_order(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = (
            '[{"sku": "coffee", "qty": 2}]'
        )

        result = await validate_order_items(str(uuid4()))

        assert result == 1

    async def test_no_items(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "[]"

        result = await validate_order_items(str(uuid4()))

        assert result == 0

    async def test_order_not_found(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await validate_order_items(str(uuid4()))

        assert result == 0


class TestYookassaCreatePayment:
    async def test_placeholder_when_no_credentials(self, _mock_session_factory):
        with patch("app.config.settings") as mock_settings:
            mock_settings.YOOKASSA_SHOP_ID = ""
            mock_settings.YOOKASSA_SECRET_KEY = ""

            result = await yookassa_create_payment(
                order_id=str(uuid4()), amount="100.00", trace_id="trace_123"
            )

            assert result == "payment_placeholder_id"


class TestYookassaVerifyPayment:
    async def test_stub_when_no_credentials(self, _mock_session_factory):
        with patch("app.config.settings") as mock_settings:
            mock_settings.YOOKASSA_SHOP_ID = ""
            mock_settings.YOOKASSA_SECRET_KEY = ""

            result = await yookassa_verify_payment(
                order_id=str(uuid4()), payment_id="pi_123"
            )

            assert result == 1


class TestWriteOrderStatePaymentPending:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "CONFIRMED"

        result = await write_order_state_payment_pending(
            order_id=str(uuid4()), payment_id="pi_123"
        )

        assert result == "OK"

    async def test_wrong_state(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        result = await write_order_state_payment_pending(
            order_id=str(uuid4()), payment_id="pi_123"
        )

        assert result == "ERROR"

    async def test_order_not_found(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await write_order_state_payment_pending(
            order_id=str(uuid4()), payment_id="pi_123"
        )

        assert result == "ERROR"


class TestWriteOrderStatePaid:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAYMENT_PENDING"

        result = await write_order_state_paid(order_id=str(uuid4()))

        assert result == "OK"

    async def test_wrong_state(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "CONFIRMED"

        result = await write_order_state_paid(order_id=str(uuid4()))

        assert result == "ERROR"


class TestWriteOrderStatePaymentFailed:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAYMENT_PENDING"

        result = await write_order_state_payment_failed(order_id=str(uuid4()))

        assert result == "OK"

    async def test_wrong_state(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        result = await write_order_state_payment_failed(order_id=str(uuid4()))

        assert result == "ERROR"


class TestWriteOrderStateCooking:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAID"

        result = await write_order_state_cooking(
            order_id=str(uuid4()), ticket_id=str(uuid4())
        )

        assert result == "OK"

    async def test_wrong_state(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        result = await write_order_state_cooking(
            order_id=str(uuid4()), ticket_id=str(uuid4())
        )

        assert result == "ERROR"


class TestReserveInventoryItems:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "10",  # stock
        ]

        result = await reserve_inventory_items(order_id=str(uuid4()))

        assert result == 1

    async def test_insufficient_stock(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "1",  # stock
        ]

        result = await reserve_inventory_items(order_id=str(uuid4()))

        assert result == 0

    async def test_order_not_found(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await reserve_inventory_items(order_id=str(uuid4()))

        assert result == 0


class TestCreateKitchenTicket:
    async def test_success(self, mock_session, _mock_session_factory):
        _mock_session_factory.return_value.__aenter__.return_value = mock_session
        ticket_id = str(uuid4())

        class FakeRow:
            def __init__(self, mapping: dict[str, object]) -> None:
                self._mapping = mapping
        mock_row = FakeRow({"id": ticket_id})
        mock_session.execute.return_value.one.return_value = mock_row

        result = await create_kitchen_ticket(order_id=str(uuid4()))

        assert result == ticket_id


class TestLogValidationFailure:
    async def test_logs_and_returns(self):
        with patch("app.tools.order_tools.logger") as mock_logger:
            result = await log_validation_failure(order_id=str(uuid4()))
            assert result == "LOGGED"
            mock_logger.warning.assert_called_once()


class TestNotifyInventoryInsufficient:
    async def test_notifies_and_returns(self):
        with patch("app.tools.order_tools.logger") as mock_logger:
            result = await notify_inventory_insufficient(order_id=str(uuid4()))
            assert result == "NOTIFIED"
            mock_logger.warning.assert_called_once()
