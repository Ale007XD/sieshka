"""
app/domains/orders/models.py
Order domain — state enum, event enum, Pydantic models.
"""
from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class OrderState(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    COOKING = "COOKING"
    PACKING = "PACKING"
    COURIER_ASSIGNED = "COURIER_ASSIGNED"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class OrderEvent(str, Enum):
    CONFIRM = "CONFIRM"
    REQUEST_PAYMENT = "REQUEST_PAYMENT"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    START_COOKING = "START_COOKING"
    START_PACKING = "START_PACKING"
    ASSIGN_COURIER = "ASSIGN_COURIER"
    PICKUP = "PICKUP"
    DELIVER = "DELIVER"
    CLOSE = "CLOSE"
    CANCEL = "CANCEL"


# Graph: allowed transitions per state
ORDER_TRANSITIONS: dict[OrderState, dict[OrderEvent, OrderState]] = {
    OrderState.DRAFT: {
        OrderEvent.CONFIRM: OrderState.CONFIRMED,
        OrderEvent.CANCEL: OrderState.CANCELLED,
    },
    OrderState.CONFIRMED: {
        OrderEvent.REQUEST_PAYMENT: OrderState.PAYMENT_PENDING,
        OrderEvent.CANCEL: OrderState.CANCELLED,
    },
    OrderState.PAYMENT_PENDING: {
        OrderEvent.PAYMENT_CONFIRMED: OrderState.PAID,
        OrderEvent.PAYMENT_FAILED: OrderState.CONFIRMED,
        OrderEvent.CANCEL: OrderState.CANCELLED,
    },
    OrderState.PAID: {
        OrderEvent.START_COOKING: OrderState.COOKING,
        OrderEvent.CANCEL: OrderState.CANCELLED,
    },
    OrderState.COOKING: {
        OrderEvent.START_PACKING: OrderState.PACKING,
    },
    OrderState.PACKING: {
        OrderEvent.ASSIGN_COURIER: OrderState.COURIER_ASSIGNED,
    },
    OrderState.COURIER_ASSIGNED: {
        OrderEvent.PICKUP: OrderState.DELIVERING,
    },
    OrderState.DELIVERING: {
        OrderEvent.DELIVER: OrderState.DELIVERED,
    },
    OrderState.DELIVERED: {
        OrderEvent.CLOSE: OrderState.CLOSED,
    },
    OrderState.CLOSED: {},
    OrderState.CANCELLED: {},
}


class OrderCreate(BaseModel):
    customer_id: UUID
    items: list[dict[str, object]] = Field(default_factory=list)
    delivery_address: str


class OrderRead(BaseModel):
    id: UUID
    customer_id: UUID
    state: OrderState
    items: list[dict[str, object]]
    delivery_address: str
    trace_id: str | None = None  # M3: wired to nano-vm trace
