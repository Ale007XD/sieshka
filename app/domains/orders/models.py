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
        OrderEvent.START_COOKING: OrderState.COOKING,  # cash orders skip payment
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
    items: list[OrderItem]
    delivery_address: str
    trace_id: str | None = None  # M3: wired to nano-vm trace


class OrderItem(BaseModel):
    """Typed, immutable snapshot of a line item as it was at order-creation time.

    Per sprint_m7_checkout_wiring: price/name are resolved ONCE from the live
    Product row via menu_service and persisted verbatim. They are NEVER
    re-joined to the mutable Product row when an existing order is later
    re-rendered (Receipt page, admin board) — a later CSV re-import that
    changes a product's price must not silently change how an ALREADY-PLACED
    order's total appears.
    """

    product_id: UUID
    name: str
    price_rub: int
    qty: int


class CheckoutItem(BaseModel):
    """Single item as sent by the real cart.js (product_id + qty only)."""

    product_id: UUID
    qty: int = Field(gt=0)


class CheckoutRequest(BaseModel):
    """sprint_m7_checkout_wiring — the REAL cart.js contract (POST /api/orders).

    Field set confirmed from cart.js::setupCheckoutForm(), NOT the earlier
    draft's guessed field set. `idempotency_key` is client-generated
    (crypto.randomUUID()) and wired into the existing IdempotencyService —
    the server does NOT invent one.
    """

    name: str
    phone: str
    address: str | None = None  # null when delivery_mode == "pickup"
    comment: str | None = None
    delivery_mode: str  # "delivery" | "pickup" (others folded into delivery)
    delivery_slot: str | None = None
    delivery_date: str | None = None
    payment_method: str  # "yookassa_card" | "cash"
    zone_id: UUID | None = None
    # BUGFIX (2026-07-19): was `int | None`. delivery_zones.id is UUID
    # (migrations/006_delivery_zones.sql) — int only ever "worked" because
    # cart.js's parseInt() coincidentally matched the 3 originally-seeded
    # zones' numeric external_id, and because nothing validated the value
    # existed (no FK on orders.zone_id until migrations/011). Any zone
    # created via sprint_m7_zone_agent's apply_zone_command has
    # external_id=NULL — parseInt() on that path always produced NaN,
    # silently dropped to null on the wire. UUID matches the real PK
    # unconditionally, regardless of how the zone was created. required +
    # validated client-side when not pickup
    items: list[CheckoutItem]
    idempotency_key: str
    client_max_uid: int | None = None  # MAX mini-app user id; persisted only
