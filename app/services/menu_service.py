from __future__ import annotations

import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import async_session_factory
from app.domains.menu.models import (
    DeliveryFeeResponse,
    MenuCategory,
    MenuProductItem,
    MenuResponse,
)


def _current_menu_period() -> Literal["morning", "evening"]:
    hour = datetime.datetime.now().hour
    return "morning" if hour < 16 else "evening"


class MenuService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def get_menu(
        self,
        method: Literal["delivery", "pickup"] = "delivery",
    ) -> MenuResponse:
        current_period = _current_menu_period()

        async with self._session_factory() as session:
            cat_rows = await self._fetch_categories(session, current_period)
            categories: list[MenuCategory] = []

            for cat in cat_rows:
                cat_id: UUID = cat._mapping["id"]
                products = await self._fetch_products(session, cat_id, current_period)
                categories.append(
                    MenuCategory(
                        category_id=cat_id,
                        name=cat._mapping["name"],
                        products=products,
                    )
                )

            return MenuResponse(categories=categories)

    async def _fetch_categories(
        self,
        session: AsyncSession,
        current_period: Literal["morning", "evening"],
    ) -> list[Any]:
        result = await session.execute(
            text(
                "SELECT id, name, menu_period "
                "FROM categories "
                "WHERE is_active = TRUE "
                "AND (menu_period = 'both' OR menu_period = :period) "
                "ORDER BY sort"
            ),
            {"period": current_period},
        )
        return list(result.fetchall())

    async def _fetch_products(
        self,
        session: AsyncSession,
        category_id: UUID,
        current_period: Literal["morning", "evening"],
    ) -> list[MenuProductItem]:
        result = await session.execute(
            text(
                "SELECT id, name, price_rub, menu_period_override, "
                "  description, image_url, is_active "
                "FROM products "
                "WHERE category_id = :category_id "
                "AND price_rub IS NOT NULL "
                "ORDER BY name"
            ),
            {"category_id": category_id},
        )
        rows = result.fetchall()
        items: list[MenuProductItem] = []

        for row in rows:
            effective_period = (
                row._mapping["menu_period_override"] or "both"
            )
            in_window = effective_period in ("both", current_period)
            is_active = row._mapping["is_active"]

            if not is_active:
                available = False
                cta_type = "unavailable"
                reason_code = "INACTIVE"
            elif not in_window:
                available = False
                cta_type = "unavailable"
                reason_code = "OUTSIDE_WINDOW"
            else:
                available = True
                cta_type = "add_to_cart"
                reason_code = None

            items.append(
                MenuProductItem(
                    product_id=row._mapping["id"],
                    name=row._mapping["name"],
                    price_rub=row._mapping["price_rub"],
                    available=available,
                    cta_type=cta_type,
                    reason_code=reason_code,
                    badge_text=None,
                    next_available=None,
                    lead_time_minutes=None,
                )
            )

        return items

    async def get_delivery_fee(self) -> DeliveryFeeResponse:
        return DeliveryFeeResponse(delivery_fee=settings.DELIVERY_FEE)
