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
def mock_session():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestCheckInventoryStock:
    async def test_returns_quantity(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "42"

        result = await check_inventory_stock(mock_session, sku="coffee")

        assert result == 42

    async def test_sku_not_found_returns_zero(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await check_inventory_stock(mock_session, sku="coffee")

        assert result == 0


class TestDecrementInventory:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "10"

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 1
        mock_session.commit.assert_not_called()

    async def test_insufficient_stock_returns_zero(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "1"

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 0

    async def test_sku_not_found_returns_zero(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = await decrement_inventory(mock_session, sku="coffee", quantity=2)

        assert result == 0


class TestIncrementInventory:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "some-id"

        result = await increment_inventory(mock_session, sku="coffee", quantity=5)

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_sku_not_found(self, mock_session):
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
    async def test_state_thresholds(self, mock_session, quantity, expected_state):
        mock_session.execute.return_value.scalar_one_or_none.return_value = str(quantity)

        result = await set_inventory_state(mock_session, sku="coffee")

        assert result == "OK"
        mock_session.commit.assert_not_called()
        update_call = mock_session.execute.call_args_list[-1]
        assert update_call.args[1]["state"] == expected_state

    async def test_sku_not_found(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="sku not found"):
            await set_inventory_state(mock_session, sku="coffee")
