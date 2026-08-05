"""tests/unit/test_inventory_service_set_quantity.py — InventoryService.set_quantity,
mocked session (no Postgres needed). sprint_inventory_restock_inline (2026-08)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domains.inventory.models import InventoryState
from app.services.inventory_service import InventoryService


def _row(mapping: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(_mapping=mapping)


def _make_session_factory() -> tuple[AsyncMock, AsyncMock]:
    session = AsyncMock()
    session.commit = AsyncMock()

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_factory, session


class TestSetQuantity:
    async def test_delegates_to_tools_and_returns_updated_row(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        set_qty = AsyncMock()
        set_state = AsyncMock()
        monkeypatch.setattr("app.tools.inventory_tools.set_inventory_quantity", set_qty)
        monkeypatch.setattr("app.tools.inventory_tools.set_inventory_state", set_state)

        session_factory, session = _make_session_factory()
        readback_result = MagicMock()
        readback_result.fetchone.return_value = _row(
            {"sku": "coffee", "name": "Coffee", "quantity": 25, "state": "AVAILABLE"}
        )
        session.execute = AsyncMock(return_value=readback_result)

        service = InventoryService(session_factory=session_factory)
        item = await service.set_quantity("coffee", 25)

        set_qty.assert_awaited_once_with(session, sku="coffee", quantity=25)
        set_state.assert_awaited_once_with(session, sku="coffee")
        session.commit.assert_awaited_once()
        assert item.sku == "coffee"
        assert item.quantity == 25
        assert item.state == InventoryState.AVAILABLE

    async def test_sku_not_found_propagates_value_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _raise_not_found(*args: object, **kwargs: object) -> None:
            raise ValueError("sku not found: coffee")

        monkeypatch.setattr(
            "app.tools.inventory_tools.set_inventory_quantity", _raise_not_found
        )

        session_factory, _session = _make_session_factory()
        service = InventoryService(session_factory=session_factory)

        with pytest.raises(ValueError, match="sku not found"):
            await service.set_quantity("coffee", 25)

    async def test_readback_missing_row_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.tools.inventory_tools.set_inventory_quantity", AsyncMock())
        monkeypatch.setattr("app.tools.inventory_tools.set_inventory_state", AsyncMock())

        session_factory, session = _make_session_factory()
        readback_result = MagicMock()
        readback_result.fetchone.return_value = None
        session.execute = AsyncMock(return_value=readback_result)

        service = InventoryService(session_factory=session_factory)

        with pytest.raises(RuntimeError, match="vanished"):
            await service.set_quantity("coffee", 25)
