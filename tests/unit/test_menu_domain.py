"""tests/unit/test_menu_domain.py — domain model validation."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.domains.menu.models import (
    Category,
    DeliveryFeeResponse,
    MenuCategory,
    MenuProductItem,
    MenuResponse,
    Product,
)


class TestCategory:
    def test_minimal(self) -> None:
        cat = Category(
            id=uuid4(),
            name="Бургеры",
            menu_period="both",
            sort=10,
            is_active=True,
        )
        assert cat.name == "Бургеры"
        assert cat.menu_period == "both"
        assert cat.parent_name is None

    def test_with_parent(self) -> None:
        cat = Category(
            id=uuid4(),
            external_id="19",
            name="Морс",
            parent_name="Напитки",
            menu_period="both",
            sort=310,
            is_active=True,
        )
        assert cat.parent_name == "Напитки"
        assert cat.external_id == "19"

    def test_invalid_menu_period(self) -> None:
        with pytest.raises(ValueError):
            Category(  # type: ignore[call-arg]
                id=uuid4(),
                name="X",
                menu_period="invalid",  # type: ignore[arg-type]
                sort=0,
                is_active=True,
            )


class TestProduct:
    def test_minimal(self) -> None:
        p = Product(id=uuid4(), name="Тестовый товар", is_active=True)
        assert p.price_rub is None
        assert p.category_id is None

    def test_with_all_fields(self) -> None:
        cat_id = uuid4()
        p = Product(
            id=uuid4(),
            name="Бургер",
            category_id=cat_id,
            menu_period_override="morning",
            price_rub=299,
            description="Вкусный бургер",
            image_url="https://example.com/burger.jpg",
            is_active=True,
        )
        assert p.category_id == cat_id
        assert p.price_rub == 299
        assert p.menu_period_override == "morning"

    def test_inactive(self) -> None:
        p = Product(id=uuid4(), name="Скрытый товар", is_active=False)
        assert p.is_active is False


class TestMenuProductItem:
    def test_available(self) -> None:
        item = MenuProductItem(
            product_id=uuid4(),
            name="Бургер",
            price_rub=299,
            available=True,
            cta_type="add_to_cart",
        )
        assert item.available is True
        assert item.cta_type == "add_to_cart"
        assert item.reason_code is None

    def test_unavailable(self) -> None:
        item = MenuProductItem(
            product_id=uuid4(),
            name="Бургер",
            price_rub=None,
            available=False,
            cta_type="unavailable",
            reason_code="OUTSIDE_WINDOW",
        )
        assert item.available is False
        assert item.reason_code == "OUTSIDE_WINDOW"


class TestMenuResponse:
    def test_empty_menu(self) -> None:
        resp = MenuResponse(categories=[])
        assert resp.categories == []

    def test_with_categories(self) -> None:
        cat_id = uuid4()
        product_id = uuid4()
        resp = MenuResponse(
            categories=[
                MenuCategory(
                    category_id=cat_id,
                    name="Бургеры",
                    products=[
                        MenuProductItem(
                            product_id=product_id,
                            name="Чизбургер",
                            price_rub=199,
                            available=True,
                            cta_type="add_to_cart",
                        ),
                    ],
                ),
            ],
        )
        assert len(resp.categories) == 1
        assert resp.categories[0].name == "Бургеры"
        assert len(resp.categories[0].products) == 1


class TestDeliveryFeeResponse:
    def test_fee(self) -> None:
        resp = DeliveryFeeResponse(delivery_fee=99)
        assert resp.delivery_fee == 99
