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
        OrderEvent.CLOSE: OrderState.CLOSED,  # pickup orders: skip delivery
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
    delivery_mode: str | None = None
    payment_method: str | None = None
    comment: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    trace_id: str | None = None  # M3: wired to nano-vm trace
    total_rub: int | None = None  # actual charged total (goods + per-zone fee
    # - promo discount) — the authoritative figure for anything that needs
    # "what did this order actually cost", vs re-deriving from current
    # settings/zone data which can drift after the order was placed
    # (2026-08-01, thanks.html delivery-fee display fix).
    discount_rub: int | None = None  # persisted promo discount snapshot, RUB
    # (migration 018) — never recomputed from live promotions.discount at
    # read time, same non-goal as total_rub above: an edited/deactivated
    # promotion must not change what an already-placed order shows it saved.


# Customer-facing status labels for thanks.html / order-confirmation surfaces.
# Deliberately separate from app.services.max_staff_notify's staff-facing
# _ORDER_STATE_TEXT (different audience, different microcopy register — no
# emoji-prefixed "🆕 Новый заказ" staff framing belongs on a page the
# customer who JUST PLACED that order is reading).
ORDER_STATE_LABELS_RU: dict[OrderState, str] = {
    OrderState.DRAFT: "Оформляется",
    OrderState.CONFIRMED: "Подтверждён",
    OrderState.PAYMENT_PENDING: "Ожидает оплаты",
    OrderState.PAID: "Оплачен",
    OrderState.COOKING: "Готовится",
    OrderState.PACKING: "Упаковывается",
    OrderState.COURIER_ASSIGNED: "Курьер назначен",
    OrderState.DELIVERING: "В пути",
    OrderState.DELIVERED: "Доставлен",
    OrderState.CLOSED: "Завершён",
    OrderState.CANCELLED: "Отменён",
}


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
    client_max_uid: int | None = None  # MAX mini-app user id; server-verified
    # (sprint_max_storefront) — checkout.py overrides whatever this field
    # carries in the client-submitted JSON with either a validate_init_data()
    # result or None; a client can no longer just claim an arbitrary id here.
    client_zalo_uid: str | None = None  # Zalo Mini App user id; server-
    # verified the same way (sprint_zalo_storefront_auth) — checkout.py
    # overrides this with a ZaloClient.get_user_profile()-verified value or
    # None, same non-trust posture as client_max_uid above.
    client_telegram_uid: int | None = None  # Telegram Mini App user id;
    # server-verified the same way as client_max_uid (sprint_telegram_
    # miniapp_auth) — checkout.py overrides this with a validate_init_data()
    # result (same function MAX uses, TELEGRAM_BOT_TOKEN instead) or None.
    promo_code: str | None = None  # separate from `comment` — see checkout.html