"""tests/unit/test_inventory_tools.py — session is closure-injected, not opened by tool.
Session provided directly by test (mocked), no async_session_factory patching needed.
Mirrors tests/unit/test_order_tools.py pattern. check_inventory_stock/decrement_inventory
keep numeric sentinels (CONDITION-consumer pattern, ASTEngine-compatible); increment_inventory/
set_inventory_state are terminal writers with no downstream CONDITION consumer — raise, not
ERROR sentinel (CONSTRAINTS.md 2026-07-02)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.inventory_tools import (
    check_inventory_stock,
    decrement_inventory,
    increment_inventory,
    set_inventory_state,
)


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestCheckInventoryStock:
    async def test_returns_quantity(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "42"

        result = await check_inventory_stock(mock_session, sku="coffee")

        assert result == 42

    async def test_sku_not_found_returns_zero(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await check_inventory_stock(mock_session, sku="coffee")

        assert result == 0


class TestDecrementInventory:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "10"

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 1
        mock_session.commit.assert_not_called()

    async def test_insufficient_stock_returns_zero(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "1"

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 0

    async def test_sku_not_found_returns_zero(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 0


class TestIncrementInventory:
    async def test_success(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = "some-id"

        result = await increment_inventory(mock_session, sku="coffee", quantity=5)

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_sku_not_found(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="sku not found"):
            await increment_inventory(mock_session, sku="coffee", quantity=5)


class TestSetInventoryState:
    @pytest.mark.parametrize(
        "quantity,expected_state",
        [
            (0, "OUT_OF_STOCK"),
            (3, "CRITICAL"),
            (10, "LOW_STOCK"),
            (25, "AVAILABLE"),
        ],
    )
    async def test_state_thresholds(
        self, mock_session: AsyncMock, quantity: int, expected_state: str,
    ) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = str(quantity)

        result = await set_inventory_state(mock_session, sku="coffee")

        assert result == "OK"
        mock_session.commit.assert_not_called()
        update_call = mock_session.execute.call_args_list[-1]
        assert update_call.args[1]["state"] == expected_state

    async def test_sku_not_found(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="sku not found"):
            await set_inventory_state(mock_session, sku="coffee")


class TestDecrementInventoryLedger:
    """sprint_inventory_ledger — decrement_inventory writes inventory_movements
    on success, does not write on failure (no quantity actually changed)."""

    async def test_success_records_negative_delta_adjustment(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = "10"

        await decrement_inventory(mock_session, sku="coffee", quantity=2)

        record.assert_awaited_once_with(
            mock_session, sku="coffee", delta=-2, reason="ADJUSTMENT",
        )

    async def test_insufficient_stock_does_not_record(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = "1"

        await decrement_inventory(mock_session, sku="coffee", quantity=2)

        record.assert_not_awaited()

    async def test_sku_not_found_does_not_record(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        await decrement_inventory(mock_session, sku="coffee", quantity=2)

        record.assert_not_awaited()


class TestIncrementInventoryLedger:
    """sprint_inventory_ledger — increment_inventory writes inventory_movements
    with a positive delta; reason defaults to RESTOCK_MANUAL, overridable via
    kwargs (RESTOCK_AGENT — used by sprint_inventory_restock_agent)."""

    async def test_success_records_positive_delta_default_reason(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = "some-id"

        await increment_inventory(mock_session, sku="coffee", quantity=5)

        record.assert_awaited_once_with(
            mock_session,
            sku="coffee",
            delta=5,
            reason="RESTOCK_MANUAL",
            source_type=None,
            source_id=None,
        )

    async def test_reason_override_restock_agent(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = "some-id"

        await increment_inventory(
            mock_session, sku="coffee", quantity=5,
            reason="RESTOCK_AGENT", source_type="chat", source_id="msg-42",
        )

        record.assert_awaited_once_with(
            mock_session,
            sku="coffee",
            delta=5,
            reason="RESTOCK_AGENT",
            source_type="chat",
            source_id="msg-42",
        )

    async def test_invalid_reason_falls_back_to_restock_manual(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = "some-id"

        await increment_inventory(mock_session, sku="coffee", quantity=5, reason="BOGUS")

        assert record.await_args is not None
        assert record.await_args.kwargs["reason"] == "RESTOCK_MANUAL"

    async def test_sku_not_found_does_not_record(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record = AsyncMock()
        monkeypatch.setattr("app.services.inventory_ledger.record_movement", record)
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="sku not found"):
            await increment_inventory(mock_session, sku="coffee", quantity=5)

        record.assert_not_awaited()
