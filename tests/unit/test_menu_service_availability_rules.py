"""tests/unit/test_menu_service_availability_rules.py — availability logic."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.menu_service import MenuService


@dataclass
class FakeRow:
    _mapping: dict[str, Any]


def _make_row(**cols: Any) -> FakeRow:
    return FakeRow(_mapping=cols)


@pytest.fixture
def service() -> MenuService:
    return MenuService()  # type: ignore[arg-type]


def _mock_session(cat_rows: list[FakeRow], prod_map: dict[UUID, list[FakeRow]]) -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()

    def execute_side_effect(stmt: Any, params: dict | None = None) -> MagicMock:
        mock_result = MagicMock()
        sql = str(stmt)

        if "FROM categories" in sql:
            mock_result.fetchall.return_value = cat_rows
        elif "FROM products" in sql and params:
            cat_id = params.get("category_id")
            mock_result.fetchall.return_value = prod_map.get(cat_id, [])
        else:
            mock_result.fetchall.return_value = []
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


class TestMenuServiceAvailability:
    async def test_all_products_available(self) -> None:
        cat_id = uuid4()
        prod_id = uuid4()
        cat_rows = [_make_row(id=cat_id, name="Бургеры", menu_period="both")]
        prod_rows = [
            _make_row(
                id=prod_id, name="Чизбургер", price_rub=199,
                menu_period_override=None, description=None, image_url=None, is_active=True,
            ),
        ]
        session = _mock_session(cat_rows, {cat_id: prod_rows})
        svc = MenuService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]

        result = await svc.get_menu(method="delivery")
        assert len(result.categories) == 1
        assert len(result.categories[0].products) == 1
        p = result.categories[0].products[0]
        assert p.available is True
        assert p.cta_type == "add_to_cart"
        assert p.reason_code is None

    async def test_category_both_period_always_returns_products(self) -> None:
        cat_id = uuid4()
        prod_id = uuid4()
        cat_rows = [_make_row(id=cat_id, name="Напитки", menu_period="both")]
        prod_rows = [
            _make_row(
                id=prod_id, name="Вода", price_rub=50,
                menu_period_override=None, description=None, image_url=None, is_active=True,
            ),
        ]
        session = _mock_session(cat_rows, {cat_id: prod_rows})
        svc = MenuService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]

        with patch("app.services.menu_service._current_menu_period", return_value="morning"):
            result = await svc.get_menu(method="delivery")
        assert len(result.categories) == 1
        assert len(result.categories[0].products) == 1
        p = result.categories[0].products[0]
        assert p.available is True
        assert p.cta_type == "add_to_cart"
        assert p.reason_code is None

    async def test_product_override_morning(self) -> None:
        cat_id = uuid4()
        prod_id = uuid4()
        cat_rows = [_make_row(id=cat_id, name="Напитки", menu_period="both")]
        prod_rows = [
            _make_row(
                id=prod_id, name="Кофе", price_rub=100,
                menu_period_override="morning", description=None, image_url=None, is_active=True,
            ),
        ]
        session = _mock_session(cat_rows, {cat_id: prod_rows})
        svc = MenuService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]

        with patch("app.services.menu_service._current_menu_period", return_value="evening"):
            result = await svc.get_menu(method="delivery")
        assert len(result.categories) == 1
        assert len(result.categories[0].products) == 1
        p = result.categories[0].products[0]
        assert p.available is False
        assert p.cta_type == "unavailable"
        assert p.reason_code == "OUTSIDE_WINDOW"


@asynccontextmanager
async def _asession(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session
