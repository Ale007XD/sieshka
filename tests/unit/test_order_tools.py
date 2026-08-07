"""tests/unit/test_order_tools.py — session is closure-injected, not opened by tool.
Session provided directly by test (mocked), no async_session_factory patching needed."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

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


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.one.return_value = None
    mock_result.fetchall.return_value = []
    session.execute.return_value = mock_result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestValidateOrderItems:
    async def test_valid_order(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = (
            '[{"sku": "coffee", "qty": 2}]'
        )

        result = await validate_order_items(mock_session, str(uuid4()))

        assert result == 1

    async def test_no_items(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "[]"

        result = await validate_order_items(mock_session, str(uuid4()))

        assert result == 0

    async def test_order_not_found(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await validate_order_items(mock_session, str(uuid4()))

        assert result == 0


class TestYookassaCreatePayment:
    async def test_placeholder_when_no_credentials(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import app.config
            mp.setattr(app.config.settings, "YOOKASSA_SHOP_ID", "")
            mp.setattr(app.config.settings, "YOOKASSA_SECRET_KEY", "")

            result = await yookassa_create_payment(
                order_id=str(uuid4()), amount="100.00", trace_id="trace_123"
            )

            assert result == "payment_placeholder_id"


class TestYookassaVerifyPayment:
    async def test_stub_when_no_credentials(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import app.config
            mp.setattr(app.config.settings, "YOOKASSA_SHOP_ID", "")
            mp.setattr(app.config.settings, "YOOKASSA_SECRET_KEY", "")

            result = await yookassa_verify_payment(
                order_id=str(uuid4()), payment_id="pi_123"
            )

            assert result == 1


class TestWriteOrderStatePaymentPending:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "CONFIRMED"

        result = await write_order_state_payment_pending(
            mock_session, order_id=str(uuid4()), payment_id="pi_123"
        )

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_order_state_payment_pending(
                mock_session, order_id=str(uuid4()), payment_id="pi_123"
            )

    async def test_order_not_found(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="order not found"):
            await write_order_state_payment_pending(
                mock_session, order_id=str(uuid4()), payment_id="pi_123"
            )


class TestWriteOrderStatePaid:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAYMENT_PENDING"

        result = await write_order_state_paid(mock_session, order_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "CONFIRMED"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_order_state_paid(mock_session, order_id=str(uuid4()))


class TestWriteOrderStatePaymentFailed:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAYMENT_PENDING"

        result = await write_order_state_payment_failed(mock_session, order_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_order_state_payment_failed(mock_session, order_id=str(uuid4()))


class TestWriteOrderStateCooking:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PAID"

        result = await write_order_state_cooking(
            mock_session, order_id=str(uuid4()), ticket_id=str(uuid4())
        )

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "DRAFT"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_order_state_cooking(
                mock_session, order_id=str(uuid4()), ticket_id=str(uuid4())
            )


class TestReserveInventoryItems:
    """sprint_inventory_sale_decrement (2026-08): reserve_inventory_items never
    blocks on stock — decrements into negative, returns 1 only as an alert
    signal (at least one item went below zero), not a failure sentinel.
    order_not_found raises now (data-integrity failure, consistent with
    write_order_state_cooking and friends in this file), it no longer returns 0."""

    async def test_all_reserved_cleanly_returns_zero(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "10",  # stock
        ]

        result = await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert result == 0
        mock_session.commit.assert_not_called()

    async def test_going_negative_returns_one_alert_signal(
        self, mock_session: AsyncMock,
    ) -> None:
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "1",  # stock — 1 - 2 = -1
        ]

        result = await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert result == 1

    async def test_order_not_found_raises(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="order not found"):
            await reserve_inventory_items(mock_session, order_id=str(uuid4()))

    async def test_sku_not_in_inventory_skips_item_no_block(
        self, mock_session: AsyncMock,
    ) -> None:
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "ghost-sku", "qty": 1}]',  # items
            None,  # stock row not found
        ]

        result = await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert result == 0

    async def test_going_negative_records_ledger_with_below_zero_true(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "1",  # stock — 1 - 2 = -1
        ]
        order_id = str(uuid4())

        await reserve_inventory_items(mock_session, order_id=order_id)

        record.assert_awaited_once_with(
            mock_session, sku="coffee", delta=-2, reason="SALE",
            source_type="order", source_id=order_id, below_zero=True,
        )

    async def test_staying_nonnegative_records_ledger_with_below_zero_false(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items
            "10",  # stock — 10 - 2 = 8
        ]
        order_id = str(uuid4())

        await reserve_inventory_items(mock_session, order_id=order_id)

        record.assert_awaited_once_with(
            mock_session, sku="coffee", delta=-2, reason="SALE",
            source_type="order", source_id=order_id, below_zero=False,
        )

    async def test_resolves_sku_via_product_id_when_item_has_no_sku(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """2026-08 fix (sale-decrement-product-id-gap, DECISIONS.md): real
        checkout orders (resolve_checkout_items/OrderItem) persist product_id,
        never sku — prior to this fix, reserve_inventory_items silently
        skipped every item of every real customer order, decrement never
        happened in production despite passing tests (tests only exercised
        the sku-present shape used by agent-created orders)."""
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        product_id = str(uuid4())
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            f'[{{"product_id": "{product_id}", "qty": 2}}]',  # items — no sku key
            "coffee",  # products.sku resolution
            "10",  # inventory stock for resolved sku
        ]
        order_id = str(uuid4())

        result = await reserve_inventory_items(mock_session, order_id=order_id)

        assert result == 0
        record.assert_awaited_once_with(
            mock_session, sku="coffee", delta=-2, reason="SALE",
            source_type="order", source_id=order_id, below_zero=False,
        )
        # verify the resolution query was keyed on the item's product_id
        resolve_call = mock_session.execute.await_args_list[1]
        assert resolve_call.args[1] == {"id": UUID(product_id)}

    async def test_product_id_with_null_sku_skips_item(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """product_id resolves to a real product row, but that product still
        has sku=NULL (never ran Generate SKUs / Sync from menu) — skip, same
        as the sku-not-in-inventory case, don't raise."""
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        product_id = str(uuid4())
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            f'[{{"product_id": "{product_id}", "qty": 1}}]',  # items
            None,  # products.sku is NULL
        ]

        result = await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert result == 0
        record.assert_not_awaited()

    async def test_no_sku_and_no_product_id_skips_item(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"qty": 1}]',  # items — neither sku nor product_id
        ]

        result = await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert result == 0
        record.assert_not_awaited()

    async def test_explicit_sku_skips_product_id_resolution_entirely(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agent-created orders (order_agent_program.py) already carry sku
        directly — must not trigger the product_id resolution query at all."""
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            '[{"sku": "coffee", "qty": 2}]',  # items — explicit sku
            "10",  # inventory stock — NOT a products.sku resolution call
        ]

        await reserve_inventory_items(mock_session, order_id=str(uuid4()))

        assert mock_session.execute.await_count == 3  # items, stock, UPDATE
        # (record_movement's own execute is on a separately-patched mock,
        # not counted against mock_session here)


class TestCreateKitchenTicket:
    async def test_success(self, mock_session: AsyncMock) -> None:
        ticket_id = str(uuid4())

        class FakeRow:
            def __init__(self, mapping: dict[str, object]) -> None:
                self._mapping = mapping
        mock_row = FakeRow({"id": ticket_id})
        mock_session.execute.return_value.one.return_value = mock_row

        result = await create_kitchen_ticket(mock_session, order_id=str(uuid4()))

        assert result == ticket_id
        mock_session.commit.assert_not_called()


class TestLogValidationFailure:
    async def test_logs_and_returns(self) -> None:
        with patch("app.tools.order_tools.logger") as mock_logger:
            result = await log_validation_failure(order_id=str(uuid4()))
            assert result == "LOGGED"
            mock_logger.warning.assert_called_once()


class TestNotifyInventoryInsufficient:
    async def test_notifies_and_returns(self) -> None:
        with patch("app.tools.order_tools.logger") as mock_logger:
            result = await notify_inventory_insufficient(order_id=str(uuid4()))
            assert result == "NOTIFIED"
            mock_logger.warning.assert_called_once()
