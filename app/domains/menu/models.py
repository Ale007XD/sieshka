from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class Category(BaseModel):
    id: UUID
    external_id: str | None = None
    name: str
    parent_name: str | None = None
    menu_period: Literal["morning", "evening", "both"]
    sort: int
    is_active: bool


class Product(BaseModel):
    id: UUID
    name: str
    category_id: UUID | None = None
    menu_period_override: Literal["morning", "evening", "both"] | None = None
    price_rub: int | None = None
    description: str | None = None
    image_url: str | None = None
    is_active: bool


class MenuProductItem(BaseModel):
    product_id: UUID
    name: str
    price_rub: int | None = None
    image_url: str | None = None
    description: str | None = None
    available: bool
    cta_type: str
    reason_code: str | None = None
    badge_text: str | None = None
    next_available: str | None = None
    lead_time_minutes: int | None = None


class MenuCategory(BaseModel):
    category_id: UUID
    name: str
    products: list[MenuProductItem]


class MenuResponse(BaseModel):
    categories: list[MenuCategory]


class DeliveryFeeResponse(BaseModel):
    delivery_fee: int
