"""tests/integration/test_inventory_stats_integration.py — movements_summary
and export_movements_csv against REAL Postgres.

Why this file exists: the unit tests (test_inventory_stats.py) mock
session.execute entirely — they prove the Python-side aggregation logic is
correct, but they can NEVER catch a bug that only manifests when SQLAlchemy
actually compiles and binds the raw SQL string. That's exactly what happened
here, twice, in sprint_inventory_stats_viz (2026-08):

  (1) First cut used `(:from_date IS NULL OR created_at >= :from_date)`.
      Every call 500'd — asyncpg couldn't determine the parameter's type
      from an `IS NULL`-only context.

  (2) The apparent fix used `:from_date::date` (Postgres cast syntax).
      This made it WORSE in a way integration tests would have caught
      immediately: SQLAlchemy's text() bind-parameter parser sees the `:`
      in `::date` as the start of a NEW bind expression and greedily
      consumes into it, producing a mis-parsed parameter name (confirmed:
      `text("...:from_date::date...")._bindparams` resolves to a key named
      `from_dat`, not `from_date` — SQLAlchemy's regex trims the trailing
      character too). The params dict passes `{"from_date": ...}`, which no
      longer matches — every call, filtered or not, 500'd.

  Fixed with `CAST(:from_date AS DATE)` — the pattern already used
  elsewhere in this codebase (app/services/idempotency.py) for exactly this
  reason. `:name` inside `CAST(...)` has no adjacent `:` to confuse the
  parser.

Both of these are real-SQL-compilation bugs — mocked-session unit tests are
structurally incapable of catching either one. This file is the regression
guard for the whole class, not just one instance of it.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from datetime import date, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.inventory_service import InventoryService

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


docker_available = _is_docker_available()


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_paths = [
        _MIGRATIONS_DIR / "001_initial_schema.sql",
        _MIGRATIONS_DIR / "016_inventory_movements.sql",
    ]
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        for sp in schema_paths:
            await conn.execute(sp.read_text())
        await conn.execute("TRUNCATE TABLE inventory_movements, inventory")
        await conn.execute(
            "INSERT INTO inventory (sku, name, quantity, state) "
            "VALUES ('coffee', 'Coffee', 10, 'AVAILABLE')"
        )
        await conn.execute(
            "INSERT INTO inventory_movements "
            "(sku, delta, reason, source_type, source_id, below_zero, created_at) "
            "VALUES "
            "('coffee', 50, 'RESTOCK_MANUAL', NULL, NULL, FALSE, $1), "
            "('coffee', -3, 'SALE', 'order', 'order-1', FALSE, $2)",
            datetime(2026, 8, 1, 9, 0, 0),
            datetime(2026, 8, 2, 15, 0, 0),
        )
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestMovementsSummaryLivePostgres:
    async def test_unfiltered_default_view_does_not_500(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """THE regression test — both dates None is the default dashboard
        load. Prior to the ::date cast fix this raised asyncpg
        'could not determine data type of parameter $1'."""
        service = InventoryService(session_factory=session_factory)

        summary = await service.movements_summary()

        assert summary.labels == ["2026-08-01", "2026-08-02"]
        assert summary.restocked == [50, 0]
        assert summary.sold == [0, 3]

    async def test_both_dates_provided_filters_correctly(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        summary = await service.movements_summary(
            from_date=date(2026, 8, 2), to_date=date(2026, 8, 2)
        )

        assert summary.labels == ["2026-08-02"]
        assert summary.sold == [3]

    async def test_only_from_date_provided(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        summary = await service.movements_summary(from_date=date(2026, 8, 2))

        assert summary.labels == ["2026-08-02"]

    async def test_only_to_date_provided(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        summary = await service.movements_summary(to_date=date(2026, 8, 1))

        assert summary.labels == ["2026-08-01"]

    async def test_range_excluding_all_data_returns_empty(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        summary = await service.movements_summary(
            from_date=date(2026, 9, 1), to_date=date(2026, 9, 30)
        )

        assert summary.labels == []


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestExportMovementsCsvLivePostgres:
    async def test_unfiltered_export_does_not_500(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert len(lines) == 3  # header + 2 rows
        assert "coffee,50,RESTOCK_MANUAL" in lines[1]
        assert "coffee,-3,SALE" in lines[2]

    async def test_filtered_export(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        service = InventoryService(session_factory=session_factory)

        csv_text = await service.export_movements_csv(
            from_date=date(2026, 8, 2), to_date=date(2026, 8, 2)
        )

        lines = csv_text.strip().splitlines()
        assert len(lines) == 2  # header + 1 row
        assert "SALE" in lines[1]
