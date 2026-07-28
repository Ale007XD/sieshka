"""app/domains/delivery/zones.py — DeliveryZone reference data.

DeliveryZone is reference data (delivery time estimates + availability per zone),
NOT a stateful entity. It is intentionally separate from the courier-assignment
DeliveryFSM in app/domains/delivery/fsm.py.

SUPERSEDED (2026-07-28, migrations/013_delivery_zones_fee.sql): the flat-fee
decision above no longer holds. delivery_fee_rub is now per-zone, authoritative
for compute_checkout_total() via ZoneService.get_by_id(). settings.DELIVERY_FEE
remains only as a fallback for pickup orders (no zone_id) or an unresolvable
zone_id — it is NOT the source of truth for delivery pricing anymore.
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
    delivery_fee_rub: int = 99
