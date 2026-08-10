"""app/domains/staff/models.py — Staff domain Pydantic models.

Mirrors the `staff` table created by migrations/017_staff.sql. Staff is a
routing lookup, not a governed entity: it has no FSM/state to transition, no
decision to interpret — it only answers "who is allowed to trigger which
transition" for the upcoming MAX/Telegram channel adapter's role_gate(). The
transitions themselves (KitchenEvent, OrderEvent) remain fully governed via
KitchenService/OrderService and are untouched by this domain.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class StaffRole(str, Enum):
    kitchen = "kitchen"
    courier = "courier"
    admin = "admin"
    staff = "staff"


class Staff(BaseModel):
    id: UUID
    name: str
    role: StaffRole
    max_user_id: int | None = None
    telegram_user_id: int | None = None
    active: bool = True
    created_at: datetime | None = None
