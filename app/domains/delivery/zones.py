"""app/domains/delivery/zones.py — DeliveryZone reference data.

DeliveryZone is reference data (delivery time estimates + availability per zone),
NOT a stateful entity. It is intentionally separate from the courier-assignment
DeliveryFSM in app/domains/delivery/fsm.py.

Ground-truth corrected (2026-07-06): real exported data carries NO price field.
Delivery pricing is a single FLAT fee (see sprint_m7_menu_domain /
GET /api/config/delivery-fee) identical regardless of zone. Zones affect ETA
and availability (is_active) only, never price.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class DeliveryZone(BaseModel):
    id: UUID
    external_id: str | None = None
    name: str
    delivery_time_minutes: int
    is_active: bool = True
