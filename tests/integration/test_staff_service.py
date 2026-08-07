"""tests/integration/test_staff_service.py — StaffService lookup tests.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.staff.models import StaffRole
from app.services.staff_service import StaffService


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_paths = [
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "017_staff.sql",
    ]
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        for sp in schema_paths:
            await conn.execute(sp.read_text())
        # Fresh table per test — assertions below are scoped by a specific
        # max_user_id/telegram_user_id, but TRUNCATE avoids UNIQUE collisions
        # across test functions within this session-scoped DB.
        await conn.execute("TRUNCATE TABLE staff")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def service(
    session_factory: async_sessionmaker[AsyncSession],
) -> StaffService:
    return StaffService(session_factory=session_factory)


async def _insert_staff(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    role: StaffRole,
    max_user_id: int | None = None,
    telegram_user_id: int | None = None,
    active: bool = True,
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO staff (name, role, max_user_id, telegram_user_id, active) "
                "VALUES (:name, :role, :max_user_id, :telegram_user_id, :active)"
            ),
            {
                "name": name,
                "role": role.value,
                "max_user_id": max_user_id,
                "telegram_user_id": telegram_user_id,
                "active": active,
            },
        )
        await session.commit()


class TestStaffService:
    async def test_find_by_max_user_id_returns_match(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: StaffService,
    ) -> None:
        await _insert_staff(
            session_factory, name="Повар Иван", role=StaffRole.kitchen, max_user_id=111
        )

        found = await service.find_by_max_user_id(111)

        assert found is not None
        assert found.name == "Повар Иван"
        assert found.role == StaffRole.kitchen
        assert found.max_user_id == 111

    async def test_find_by_max_user_id_unknown_returns_none(
        self, service: StaffService
    ) -> None:
        assert await service.find_by_max_user_id(999999) is None

    async def test_find_by_telegram_user_id_returns_match(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: StaffService,
    ) -> None:
        await _insert_staff(
            session_factory,
            name="Курьер Пётр",
            role=StaffRole.courier,
            telegram_user_id=222,
        )

        found = await service.find_by_telegram_user_id(222)

        assert found is not None
        assert found.role == StaffRole.courier
        assert found.telegram_user_id == 222

    async def test_inactive_staff_not_returned(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: StaffService,
    ) -> None:
        await _insert_staff(
            session_factory,
            name="Уволенный админ",
            role=StaffRole.admin,
            max_user_id=333,
            active=False,
        )

        assert await service.find_by_max_user_id(333) is None

    async def test_list_active_by_role_filters_correctly(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: StaffService,
    ) -> None:
        await _insert_staff(
            session_factory, name="Кухня 1", role=StaffRole.kitchen, max_user_id=444
        )
        await _insert_staff(
            session_factory, name="Кухня 2", role=StaffRole.kitchen, max_user_id=555
        )
        await _insert_staff(
            session_factory, name="Курьер 1", role=StaffRole.courier, max_user_id=666
        )

        kitchen_staff = await service.list_active_by_role(StaffRole.kitchen)

        assert {s.max_user_id for s in kitchen_staff} == {444, 555}
        assert all(s.role == StaffRole.kitchen for s in kitchen_staff)

    async def test_role_check_constraint_rejects_invalid_role(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with session_factory() as session:
            with pytest.raises(Exception):
                await session.execute(
                    text(
                        "INSERT INTO staff (name, role) VALUES (:name, :role)"
                    ),
                    {"name": "Кто-то", "role": "manager"},
                )
                await session.commit()

    async def test_max_user_id_unique_constraint(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _insert_staff(
            session_factory, name="Первый", role=StaffRole.admin, max_user_id=777
        )
        with pytest.raises(Exception):
            await _insert_staff(
                session_factory, name="Второй", role=StaffRole.admin, max_user_id=777
            )
