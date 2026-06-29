from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel

from app.domains.delivery.fsm import DeliveryEvent, DeliveryState
from app.fsm.core.base import TransitionResult
from app.services.delivery_service import DeliveryService, DeliveryTaskRead

router = APIRouter(prefix="/delivery", tags=["delivery"])


class DeliveryTaskCreate(BaseModel):
    order_id: UUID


def get_delivery_service() -> DeliveryService:
    return DeliveryService()


@router.get("/tasks")
async def list_tasks(
    state: DeliveryState | None = Query(None),
    service: DeliveryService = Depends(get_delivery_service),
) -> list[DeliveryTaskRead]:
    return await service.list_tasks(state_filter=state)


@router.post("/tasks", status_code=201)
async def create_task(
    body: DeliveryTaskCreate,
    service: DeliveryService = Depends(get_delivery_service),
) -> DeliveryTaskRead:
    return await service.create_task(str(body.order_id))


@router.post("/tasks/{task_id}/events")
async def trigger_event(
    task_id: UUID,
    body: DeliveryEvent = Body(...),
    service: DeliveryService = Depends(get_delivery_service),
) -> TransitionResult:
    return await service.transition_task(str(task_id), body)
