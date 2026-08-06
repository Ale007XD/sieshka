"""app/services/staff_service.py — Staff role lookup by messenger identity.

Bare service, same non-goal class as CustomerService.find_or_create_by_phone
(app/services/customer_service.py): a routing lookup, not a governed
transition — there is no FSM/state here for a nano-vm Program to own. The
upcoming MAX/Telegram channel adapter's role_gate() calls find_by_max_user_id()
/ find_by_telegram_user_id() to resolve a role BEFORE dispatching into the
actually-governed KitchenService/OrderService transitions; this service never
calls either of those itself and never mutates order/kitchen state.

Row creation is deliberately NOT exposed here yet (sprint_staff_table scope is
lookup only) — staff rows are seeded directly (psql / a future admin screen)
until an explicit onboarding flow is designed.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.staff.models import Staff, StaffRole

logger = logging.getLogger(__name__)

_SELECT_COLUMNS = (
    "id, name, role, max_user_id, telegram_user_id, active, created_at"
)


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
    """Read-only lookup of staff by messenger identity."""

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