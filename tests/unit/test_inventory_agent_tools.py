"""tests/unit/test_inventory_agent_tools.py — InventoryAgent tool functions.
sprint_inventory_restock_agent (2026-08). Mocked session, no Postgres."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.inventory_agent_tools import (
    _required_apply_fields,
    apply_restock_command,
    collect_restock_command,
    report_invalid_restock_command,
    validate_apply_restock_command,
    validate_restock_command,
)

_VALID_RESTOCK_JSON = '{"sku": "burger-firm", "quantity": 50}'


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    session.execute.return_value = mock_result
    return session


class TestValidateRestockCommand:
    async def test_valid_command(self) -> None:
        result = await validate_restock_command(_VALID_RESTOCK_JSON)
        assert result == 1

    async def test_empty_instruction(self) -> None:
        diagnostics: dict[str, str] = {}
        result = await validate_restock_command("", diagnostics=diagnostics)
        assert result == 0
        assert "empty" in diagnostics["reason"]

    async def test_not_json(self) -> None:
        diagnostics: dict[str, str] = {}
        result = await validate_restock_command("not json", diagnostics=diagnostics)
        assert result == 0
        assert "could not parse" in diagnostics["reason"]

    async def test_not_an_object(self) -> None:
        result = await validate_restock_command("[1, 2, 3]")
        assert result == 0

    async def test_missing_sku(self) -> None:
        diagnostics: dict[str, str] = {}
        result = await validate_restock_command(
            '{"quantity": 5}', diagnostics=diagnostics
        )
        assert result == 0
        assert "sku" in diagnostics["reason"]

    async def test_empty_sku(self) -> None:
        result = await validate_restock_command('{"sku": "  ", "quantity": 5}')
        assert result == 0

    async def test_missing_quantity(self) -> None:
        diagnostics: dict[str, str] = {}
        result = await validate_restock_command(
            '{"sku": "coffee"}', diagnostics=diagnostics
        )
        assert result == 0
        assert "quantity" in diagnostics["reason"]

    async def test_quantity_not_int(self) -> None:
        result = await validate_restock_command('{"sku": "coffee", "quantity": "many"}')
        assert result == 0

    async def test_quantity_bool_rejected(self) -> None:
        # bool is a subclass of int in Python — must be explicitly excluded
        result = await validate_restock_command('{"sku": "coffee", "quantity": true}')
        assert result == 0

    async def test_quantity_zero_rejected(self) -> None:
        diagnostics: dict[str, str] = {}
        result = await validate_restock_command(
            '{"sku": "coffee", "quantity": 0}', diagnostics=diagnostics
        )
        assert result == 0
        assert "positive" in diagnostics["reason"]

    async def test_quantity_negative_rejected(self) -> None:
        result = await validate_restock_command('{"sku": "coffee", "quantity": -5}')
        assert result == 0


class TestCollectRestockCommand:
    async def test_returns_command_unchanged(self) -> None:
        result = await collect_restock_command(_VALID_RESTOCK_JSON)
        assert result == _VALID_RESTOCK_JSON


class TestRequiredApplyFields:
    def test_well_formed(self) -> None:
        parsed = _required_apply_fields({"sku": "coffee", "quantity": 10})
        assert parsed == ("coffee", 10)

    def test_strips_sku(self) -> None:
        parsed = _required_apply_fields({"sku": "  coffee  ", "quantity": 10})
        assert parsed == ("coffee", 10)

    def test_not_a_dict(self) -> None:
        assert _required_apply_fields("not a dict") is None

    def test_missing_sku(self) -> None:
        assert _required_apply_fields({"quantity": 10}) is None

    def test_non_positive_quantity(self) -> None:
        assert _required_apply_fields({"sku": "coffee", "quantity": 0}) is None
        assert _required_apply_fields({"sku": "coffee", "quantity": -1}) is None

    def test_bool_quantity_rejected(self) -> None:
        assert _required_apply_fields({"sku": "coffee", "quantity": True}) is None


class TestValidateApplyRestockCommand:
    async def test_sku_exists_accepts(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.fetchall.return_value = [MagicMock()]

        result = await validate_apply_restock_command(
            mock_session, {"sku": "coffee", "quantity": 10}
        )

        assert result == 1

    async def test_sku_not_found_rejects(self, mock_session: AsyncMock) -> None:
        mock_session.execute.return_value.fetchall.return_value = []
        diagnostics: dict[str, str] = {}

        result = await validate_apply_restock_command(
            mock_session, {"sku": "ghost", "quantity": 10}, diagnostics=diagnostics
        )

        assert result == 0
        assert "not found" in diagnostics["reason"]

    async def test_malformed_command_rejects(self, mock_session: AsyncMock) -> None:
        result = await validate_apply_restock_command(mock_session, {"quantity": 10})
        assert result == 0


class TestApplyRestockCommand:
    async def test_delegates_to_increment_inventory(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        increment = AsyncMock(return_value="OK")
        monkeypatch.setattr("app.tools.inventory_tools.increment_inventory", increment)

        result = await apply_restock_command(
            mock_session, {"sku": "coffee", "quantity": 15}
        )

        assert result == {"applied": True, "sku": "coffee", "quantity": 15}
        increment.assert_awaited_once_with(
            mock_session, sku="coffee", quantity=15, reason="RESTOCK_AGENT",
            source_type="agent",
        )

    async def test_malformed_command_raises(self, mock_session: AsyncMock) -> None:
        with pytest.raises(ValueError, match="malformed command"):
            await apply_restock_command(mock_session, {"quantity": 10})

    async def test_sku_not_found_propagates_from_increment_inventory(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _raise_not_found(*args: object, **kwargs: object) -> str:
            raise ValueError("sku not found: ghost")

        monkeypatch.setattr(
            "app.tools.inventory_tools.increment_inventory", _raise_not_found
        )

        with pytest.raises(ValueError, match="sku not found"):
            await apply_restock_command(mock_session, {"sku": "ghost", "quantity": 5})


class TestReportInvalidRestockCommand:
    async def test_returns_rejected_sentinel(self) -> None:
        result = await report_invalid_restock_command("sku not found")
        assert result == "REJECTED:sku not found"
