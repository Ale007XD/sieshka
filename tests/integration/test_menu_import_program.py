"""tests/integration/test_menu_import_program.py — full CSV import via ExecutionVM.

Requires Docker (testcontainers/sieshka-postgres). Skipped if unavailable.
Mirrors tests/integration/test_execution_vm_orders.py: a real ExecutionVM is
built with a GovernedToolExecutor whose .check() is spied on, so we can prove
the import genuinely runs through the Program (and that governance is enforced)
rather than a raw, unwrapped DB write. A second test exercises the full
MenuImportService.import_csv path end-to-end.
"""
from __future__ import annotations

import functools
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import Trace, TraceStatus
from nano_vm.vm import ExecutionVM
from nano_vm_mcp.handlers import GovernedToolExecutor
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import MENU_IMPORT_POLICY_SNAPSHOT
from app.programs.menu_import_program import MENU_IMPORT_PROGRAM
from app.services.menu_import_service import (
    MenuImportService,
    ProductRow,
    _governed_tool,
)
from app.tools.menu_import_tools import apply_menu_import

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def nano_store_path() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
async def session(postgres_dsn: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(postgres_dsn)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        # Clean slate so tests don't see each other's products/categories.
        await s.execute(text("DELETE FROM products"))
        await s.execute(text("DELETE FROM categories"))
        await s.commit()
        yield s
    await engine.dispose()


async def _seed_categories(session: AsyncSession) -> dict[str, UUID]:
    rows = [
        ("1", "Бургеры"),
        ("8", "!!!КОМБО!!!"),  # seeded WITHOUT spaces
        ("19", "Морс"),
        ("21", "Вода"),
    ]
    for ext, name in rows:
        await session.execute(
            text(
                "INSERT INTO categories (external_id, name, menu_period, sort, is_active) "
                "VALUES (:ext, :name, 'both', 10, TRUE)"
            ),
            {"ext": ext, "name": name},
        )
    await session.commit()
    res = await session.execute(text("SELECT external_id, id FROM categories"))
    return {r._mapping["external_id"]: r._mapping["id"] for r in res}


def _build_vm(
    session: AsyncSession,
    executor: object,
    nano_store_path: str,
) -> ExecutionVM:
    from nano_vm_mcp.store import ProgramStore

    store = ProgramStore(nano_store_path)
    cursor = StoreCursorRepository(store)
    vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
    governed = _governed_tool(apply_menu_import, "apply_menu_import", executor)
    vm.register_tool(
        "apply_menu_import",
        functools.partial(governed, session=session),
    )
    return vm


class TestMenuImportViaExecutionVM:
    async def test_import_runs_through_program_with_governance(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        cats = await _seed_categories(session)

        valid_rows = [
            ProductRow(
                name="Burger", category_id=cats["1"], price_rub=350, is_active=True
            ),
            ProductRow(
                name="Water", category_id=cats["21"], price_rub=50, is_active=True
            ),
            # Whitespace-normalized name match against seeded "!!!КОМБО!!!".
            ProductRow(name="Kombo", category_id=cats["8"], price_rub=700, is_active=True),
        ]


        executor = GovernedToolExecutor(policy=MENU_IMPORT_POLICY_SNAPSHOT)
        with patch.object(executor, "check", wraps=executor.check) as spy:
            vm = _build_vm(session, executor, nano_store_path)
            trace: Trace = await vm.run(
                MENU_IMPORT_PROGRAM,
                context={"valid_rows": [r.model_dump() for r in valid_rows]},
            )

            assert trace.status == TraceStatus.SUCCESS
            # Governance was actually enforced (not a raw unwrapped DB write).
            spy.assert_any_call("apply_menu_import")

        await session.commit()

        res = await session.execute(
            text("SELECT name, price_rub, is_active FROM products ORDER BY name")
        )
        rows = res.fetchall()
        assert len(rows) == 3
        names = {r._mapping["name"]: r._mapping["price_rub"] for r in rows}
        assert names["Burger"] == 350
        assert names["Water"] == 50
        assert names["Kombo"] == 700

    async def test_full_service_import_csv(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        await _seed_categories(session)

        csv_bytes = (
            b"Name,Category,Description,Price Rub,Photo Url\n"
            b"Burger,Burgers,tasty,350,http://img/burger.png\n"  # category by name
            b"Water,21,,50,\n"  # category by external_id
            b"Fries,,\n"  # blank category + blank price -> unassigned + inactive
            b"Bad,NoSuchCat,,99,\n"  # unknown category -> skipped
        )

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        service = MenuImportService(session_factory=sf)

        report = await service.import_csv(csv_bytes)

        assert report.final_status == "SUCCESS"
        # Burger, Water, Fries imported; Bad skipped (unknown category).
        assert report.imported == 3
        assert len(report.skipped) == 1
        assert report.skipped[0].reason == "unknown category: NoSuchCat"

        # Verify persisted state.
        res = await session.execute(
            text("SELECT name, price_rub, is_active, category_id FROM products")
        )
        by_name = {r._mapping["name"]: r for r in res.fetchall()}
        assert set(by_name) == {"Burger", "Water", "Fries"}
        assert by_name["Fries"]._mapping["price_rub"] is None
        assert by_name["Fries"]._mapping["is_active"] is False
        assert by_name["Burger"]._mapping["category_id"] is not None

        await engine.dispose()
