from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel

from app.domains.kitchen.fsm import KitchenEvent, KitchenState
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService, KitchenTicketRead

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


class KitchenTicketCreate(BaseModel):
    order_id: UUID


def get_kitchen_service() -> KitchenService:
    return KitchenService()


@router.get("/tickets")
async def list_tickets(
    state: KitchenState | None = Query(None),
    service: KitchenService = Depends(get_kitchen_service),
) -> list[KitchenTicketRead]:
    return await service.list_tickets(state_filter=state)


@router.post("/tickets", status_code=201)
async def create_ticket(
    body: KitchenTicketCreate,
    service: KitchenService = Depends(get_kitchen_service),
) -> KitchenTicketRead:
    return await service.create_ticket(str(body.order_id))


@router.post("/tickets/{ticket_id}/events")
async def trigger_event(
    ticket_id: UUID,
    body: KitchenEvent = Body(...),
    service: KitchenService = Depends(get_kitchen_service),
) -> TransitionResult:
    return await service.transition_ticket(str(ticket_id), body)
