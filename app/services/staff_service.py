"""app/services/staff_service.py — Staff role lookup by messenger identity.

Bare service, same non-goal class as CustomerService.find_or_create_by_phone
(app/services/customer_service.py): a routing lookup, not a governed
transition — there is no FSM/state here for a nano-vm Program to own. The
MAX/Telegram channel adapter's role_gate() calls find_by_max_user_id()/
find_by_telegram_user_id() to resolve a role BEFORE dispatching into the
actually-governed KitchenService/OrderService transitions; this service never
calls either of those itself and never mutates order/kitchen state.

sprint_max_admin_panel: adds the admin CRUD (list_all/get_by_id/create/
update) that sprint_staff_table's docstring originally deferred ("staff rows
are seeded directly (psql / a future admin screen) until an explicit
onboarding flow is designed" — this IS that screen). Deliberately still NOT
routed through a nano-vm Agent/Program, unlike every other admin.py entity
(zones/menu/promotions) — those are governed because they have real business
FSMs; staff has none (sprint_staff_table's own architectural decision).
Wrapping a lookup table in Program/Receipt machinery it doesn't need would
be governance theater, not governance.
"""
from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.staff.models import Staff, StaffRole

logger = logging.getLogger(__name__)

_SELECT_COLUMNS = (
    "id, name, role, max_user_id, telegram_user_id, active, created_at"
)

# Fields update() is allowed to touch — an explicit allowlist, not
# **payload passthrough, so a stray key in an admin form can never reach the
# SQL SET clause.
_UPDATABLE_FIELDS = ("name", "role", "max_user_id", "telegram_user_id", "active")


class StaffConflictError(Exception):
    """A max_user_id/telegram_user_id is already assigned to another staff
    row (partial UNIQUE index violation, migrations/017_staff.sql). Raised
    instead of letting asyncpg's IntegrityError leak past this service, so
    the admin route can turn it into a clean 409, not a raw 500."""


def _row_to_staff(row: object) -> Staff:
    mapping = row._mapping  # type: ignore[attr-defined]
    return Staff(
        id=mapping["id"],
        name=mapping["name"],
        role=StaffRole(mapping["role"]),
        max_user_id=mapping["max_user_id"],
        telegram_user_id=mapping["telegram_user_id"],
        active=mapping["active"],
        created_at=mapping["created_at"],
    )


class StaffService:
    """Lookup of staff by messenger identity, plus admin CRUD."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def find_by_max_user_id(self, max_user_id: int) -> Staff | None:
        async with self._session_factory() as session:
            return await self._find_one(
                session, "max_user_id = :value", max_user_id
            )

    async def find_by_telegram_user_id(self, telegram_user_id: int) -> Staff | None:
        async with self._session_factory() as session:
            return await self._find_one(
                session, "telegram_user_id = :value", telegram_user_id
            )

    async def list_active_by_role(self, role: StaffRole) -> list[Staff]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM staff "
                    "WHERE role = :role AND active"
                ),
                {"role": role.value},
            )
            return [_row_to_staff(row) for row in result.fetchall()]

    async def list_all(self) -> list[Staff]:
        """Admin panel listing — every row, active or not (an inactive row
        must still be visible/reactivatable, unlike the role_gate lookups
        above, which deliberately filter to active-only)."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM staff ORDER BY created_at")
            )
            return [_row_to_staff(row) for row in result.fetchall()]

    async def get_by_id(self, staff_id: UUID) -> Staff | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM staff WHERE id = :id"),
                {"id": staff_id},
            )
            row = result.fetchone()
            return _row_to_staff(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        role: StaffRole,
        max_user_id: int | None = None,
        telegram_user_id: int | None = None,
    ) -> Staff:
        staff_id = uuid4()
        async with self._session_factory() as session:
            try:
                await session.execute(
                    text(
                        "INSERT INTO staff "
                        "(id, name, role, max_user_id, telegram_user_id, active) "
                        "VALUES (:id, :name, :role, :max_user_id, :telegram_user_id, true)"
                    ),
                    {
                        "id": staff_id,
                        "name": name,
                        "role": role.value,
                        "max_user_id": max_user_id,
                        "telegram_user_id": telegram_user_id,
                    },
                )
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                logger.warning("StaffService.create: conflict for name=%r: %s", name, e)
                raise StaffConflictError(
                    "max_user_id or telegram_user_id already assigned to another staff row"
                ) from e

        created = await self.get_by_id(staff_id)
        assert created is not None  # just inserted, in the same call
        return created

    async def update(self, staff_id: UUID, payload: dict[str, object]) -> Staff | None:
        """Partial update. PATCH semantics via dict-key presence, not a
        COALESCE(new, old) SQL pattern: a key ABSENT from payload leaves that
        column untouched; a key PRESENT with value None explicitly clears it
        (needed here — unlike every other COALESCE-partial-update entity in
        this codebase, unlinking a messenger id, i.e. setting max_user_id
        back to NULL, is a real, intended operation, not a client mistake to
        guard against).

        Returns the updated row, or None if staff_id doesn't exist.
        """
        fields = {k: v for k, v in payload.items() if k in _UPDATABLE_FIELDS}
        if not fields:
            return await self.get_by_id(staff_id)

        if "role" in fields and fields["role"] is not None:
            fields["role"] = StaffRole(fields["role"]).value

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        async with self._session_factory() as session:
            try:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        text(f"UPDATE staff SET {set_clause} WHERE id = :id"),
                        {**fields, "id": staff_id},
                    ),
                )
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                logger.warning(
                    "StaffService.update: conflict for staff_id=%s: %s", staff_id, e
                )
                raise StaffConflictError(
                    "max_user_id or telegram_user_id already assigned to another staff row"
                ) from e

            if result.rowcount == 0:
                return None

        return await self.get_by_id(staff_id)

    async def _find_one(
        self, session: AsyncSession, where_clause: str, value: int
    ) -> Staff | None:
        result = await session.execute(
            text(
                f"SELECT {_SELECT_COLUMNS} FROM staff "
                f"WHERE {where_clause} AND active"
            ),
            {"value": value},
        )
        row = result.fetchone()
        if row is None:
            return None
        return _row_to_staff(row)
