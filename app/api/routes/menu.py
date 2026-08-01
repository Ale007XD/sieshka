# /app/api/routes/menu.py
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.domains.delivery.zones import DeliveryZone
from app.domains.menu.models import DeliveryFeeResponse, MenuResponse
from app.services.menu_service import MenuService

router = APIRouter(prefix="/api", tags=["menu"])


def get_menu_service() -> MenuService:
    return MenuService()


@router.get("/menu")
async def get_menu(
    method: Literal["delivery", "pickup"] = Query("delivery"),
    service: MenuService = Depends(get_menu_service),
) -> MenuResponse:
    return await service.get_menu(method=method)


@router.get("/config/delivery-fee")
async def get_delivery_fee(
    zone_id: UUID | None = Query(None),
    service: MenuService = Depends(get_menu_service),
) -> DeliveryFeeResponse:
    return await service.get_delivery_fee(zone_id=zone_id)


@router.get("/delivery-zones")
async def get_delivery_zones(
    service: MenuService = Depends(get_menu_service),
) -> list[DeliveryZone]:
    return await service.get_delivery_zones()
