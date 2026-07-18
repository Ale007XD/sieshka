"""app/services/zone_service.py — read access to DeliveryZone reference data.

The public GET /api/delivery-zones (app/services/menu_service.py:
get_delivery_zones) filters to is_active = TRUE. This service is the admin-side
counterpart: it lists ALL zones (active and soft-deleted) so an admin can see
what was retired and still reference it, and it resolves a single zone by id
(even a retired one) to prove it stays resolvable after deactivation.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.delivery.zones import DeliveryZone


class ZoneService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def list_all(self) -> list[DeliveryZone]:
        """Return every zone, active or retired, ordered by ETA then name.

        Mock the public endpoint's active-only filter: an admin needs to see
        retired zones in the reference table too.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, external_id, name, delivery_time_minutes, is_active "
                    "FROM delivery_zones "
                    "ORDER BY is_active DESC, delivery_time_minutes, lower(name)"
                )
            )
            rows = result.fetchall()
        return [
            DeliveryZone(
                id=row._mapping["id"],
                external_id=row._mapping["external_id"],
                name=row._mapping["name"],
                delivery_time_minutes=row._mapping["delivery_time_minutes"],
                is_active=row._mapping["is_active"],
            )
            for row in rows
        ]

    async def get_by_id(self, zone_id: UUID) -> DeliveryZone | None:
        """Resolve a single zone by id, even if it is retired (soft-deleted).

        Proves the sprint's invariant: a deactivated zone stays resolvable for
        any past order's zone_id. Returns None only if the row was hard-deleted
        (which this codebase never does).
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, external_id, name, delivery_time_minutes, is_active "
                    "FROM delivery_zones WHERE id = :id"
                ),
                {"id": zone_id},
            )
            row = result.one_or_none()
        if row is None:
            return None
        return DeliveryZone(
            id=row._mapping["id"],
            external_id=row._mapping["external_id"],
            name=row._mapping["name"],
            delivery_time_minutes=row._mapping["delivery_time_minutes"],
            is_active=row._mapping["is_active"],
        )
