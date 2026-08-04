"""tests/unit/test_inventory_ledger.py — record_movement is a thin INSERT
wrapper, caller controls the transaction (no commit inside)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.inventory_ledger import record_movement


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


class TestRecordMovement:
    async def test_executes_insert_with_expected_params(
        self, mock_session: AsyncMock,
    ) -> None:
        await record_movement(
            mock_session, sku="coffee", delta=-2, reason="SALE",
            source_type="order", source_id="order-1",
        )

        mock_session.execute.assert_awaited_once()
        params = mock_session.execute.call_args.args[1]
        assert params == {
            "sku": "coffee",
            "delta": -2,
            "reason": "SALE",
            "source_type": "order",
            "source_id": "order-1",
            "below_zero": False,
        }

    async def test_does_not_commit(self, mock_session: AsyncMock) -> None:
        await record_movement(mock_session, sku="coffee", delta=5, reason="RESTOCK_MANUAL")

        mock_session.commit.assert_not_awaited()

    async def test_below_zero_flag_passed_through(self, mock_session: AsyncMock) -> None:
        await record_movement(
            mock_session, sku="coffee", delta=-5, reason="SALE", below_zero=True,
        )

        params = mock_session.execute.call_args.args[1]
        assert params["below_zero"] is True

    async def test_defaults_source_fields_to_none(self, mock_session: AsyncMock) -> None:
        await record_movement(mock_session, sku="coffee", delta=1, reason="ADJUSTMENT")

        params = mock_session.execute.call_args.args[1]
        assert params["source_type"] is None
        assert params["source_id"] is None
