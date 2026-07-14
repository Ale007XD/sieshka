"""tests/unit/test_menu_service_availability_rules.py — availability logic."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.menu_service import MenuService, _current_menu_period


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

    async def test_inactive_product_shows_unavailable(self) -> None:
        cat_id = uuid4()
        prod_id = uuid4()
        cat_rows = [_make_row(id=cat_id, name="Бургеры", menu_period="both")]
        prod_rows = [
            _make_row(
                id=prod_id, name="Старый бургер", price_rub=199,
                menu_period_override=None, description=None, image_url=None,
                is_active=False,
            ),
        ]
        session = _mock_session(cat_rows, {cat_id: prod_rows})
        svc = MenuService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]

        result = await svc.get_menu(method="delivery")
        assert len(result.categories) == 1
        assert len(result.categories[0].products) == 1
        p = result.categories[0].products[0]
        assert p.available is False
        assert p.cta_type == "unavailable"
        assert p.reason_code == "INACTIVE"

    async def test_product_inherits_category_period(self) -> None:
        cat_id = uuid4()
        prod_id = uuid4()
        cat_rows = [_make_row(id=cat_id, name="Напитки", menu_period="morning")]
        prod_rows = [
            _make_row(
                id=prod_id, name="Кофе", price_rub=100,
                menu_period_override=None, description=None, image_url=None,
                is_active=True,
            ),
        ]
        session = _mock_session(cat_rows, {cat_id: prod_rows})
        svc = MenuService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]

        # Mock bypasses the category period filter; product with no override must
        # inherit the category's "morning" period and be OUTSIDE_WINDOW in the evening.
        with patch("app.services.menu_service._current_menu_period", return_value="evening"):
            result = await svc.get_menu(method="delivery")
        p = result.categories[0].products[0]
        assert p.available is False
        assert p.reason_code == "OUTSIDE_WINDOW"
    def test_current_menu_period_uses_configured_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types
        from datetime import datetime as _RealDateTime
        from zoneinfo import ZoneInfo

        monkeypatch.setattr(
            "app.services.menu_service.settings.MENU_TIMEZONE", "UTC"
        )
        monkeypatch.setattr(
            "app.services.menu_service.settings.MENU_MORNING_END_HOUR", 16
        )
        tz = ZoneInfo("UTC")

        class _FixedDateTime(_RealDateTime):
            @classmethod
            def now(cls, _tz: object = None) -> _RealDateTime:
                return _fixed_value  # type: ignore[used-before-def]

        morning = _RealDateTime(2026, 1, 1, 15, 30, tzinfo=tz)
        evening = _RealDateTime(2026, 1, 1, 17, 0, tzinfo=tz)

        _fake_dt = types.SimpleNamespace(datetime=_FixedDateTime)
        for _fixed_value in (morning, evening):
            monkeypatch.setattr("app.services.menu_service.datetime", _fake_dt)
            expected = "morning" if _fixed_value.hour < 16 else "evening"
            assert _current_menu_period() == expected



@asynccontextmanager
async def _asession(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session
