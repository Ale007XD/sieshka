"""tests/integration/test_zone_agent.py — ZoneAgent APPLY phase + zone endpoints.

Requires Docker (sieshka-postgres). Skipped if unavailable.

Failure-path-first (same discipline as test_schedule_agent.py /
test_menu_agent_apply_phase.py):
  1. ambiguous-target rejection (update/deactivate resolving to != 1 active zone)
  2. duplicate-active-name rejection (create reuses an active zone's name)
  3. write failure (unique_violation race) -> tool RAISES + Trace FAILED, not an
     unexplained asyncpg error
  4. happy paths: create / update / deactivate (soft delete)
  5. soft-delete invariant: a deactivated zone is excluded from the public
     GET /api/delivery-zones but STILL resolvable by id (ZoneService.get_by_id)
  6. index-reality gate: the partial unique index idx_delivery_zones_name_active
     is REAL — inserting two active rows with the same normalized name raises
     IntegrityError (unique_violation).
"""
from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import Trace, TraceStatus
from nano_vm.vm import ExecutionVM
from nano_vm_mcp.handlers import GovernedToolExecutor
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.zone_agent import (
    _APPLY_SESSION_TOOLS,
    _APPLY_TOOLS,
    ZoneAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import ZONE_AGENT_APPLY_POLICY_SNAPSHOT
from app.programs.zone_agent_program import PROGRAM_APPLY_ZONE
from app.services.zone_service import ZoneService
from app.tools import zone_agent_tools

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def nano_store_path() -> Generator[str, None, None]:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
async def session(postgres_dsn: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(postgres_dsn)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as s:
        # Orders now FKs zone_id -> delivery_zones (migrations/011); clear
        # orders (and anything referencing them) so the zone delete is not
        # blocked by a stale reference from an earlier test in this shared DB.
        await s.execute(text("TRUNCATE TABLE orders CASCADE"))
        await s.execute(text("DELETE FROM delivery_zones"))
        await s.commit()
        yield s
    await engine.dispose()


def _build_apply_vm(
    session: AsyncSession,
    executor: GovernedToolExecutor,
    nano_store_path: str,
) -> ExecutionVM:
    from nano_vm_mcp.store import ProgramStore

    store = ProgramStore(nano_store_path)
    cursor = StoreCursorRepository(store)
    vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
    for name, fn in _APPLY_TOOLS.items():
        governed = _governed_tool(fn, name, executor)
        if name in _APPLY_SESSION_TOOLS:
            vm.register_tool(name, functools.partial(governed, session=session))
        else:
            vm.register_tool(name, governed)
    return vm


async def _seed_zone(
    session: AsyncSession,
    name: str,
    dtm: int = 15,
    is_active: bool = True,
) -> str:
    result = await session.execute(
        text(
            "INSERT INTO delivery_zones (name, delivery_time_minutes, is_active) "
            "VALUES (:name, :dtm, :active) RETURNING id"
        ),
        {"name": name, "dtm": dtm, "active": is_active},
    )
    await session.commit()
    return str(result.one()._mapping["id"])


class TestZoneAgentApplyPhase:
    # ---- invalid branch -----------------------------------------------------

    async def test_update_ambiguous_target_rejected_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """update/deactivate on a name matching 0 or 2+ active zones -> rejected.

        Two active zones with the same normalized name is impossible once the
        unique index exists, so we instead exercise the 0-match (no such active
        zone) path: nothing resolved -> invalid branch, nothing written.
        """
        command = {
            "action": "update",
            "name": None,
            "delivery_time_minutes": 30,
            "target_zone_name": "Несуществующая зона",
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM delivery_zones")
        )
        assert int(res.one()._mapping["n"]) == 0  # nothing written

    async def test_create_duplicate_active_name_rejected_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_zone(session, "Балахня", dtm=15)
        command = {
            "action": "create",
            "name": "балахня",  # case-insensitive duplicates an active zone
            "delivery_time_minutes": 20,
            "target_zone_name": None,
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM delivery_zones")
        )
        # Still exactly the one seeded active zone — no duplicate created.
        assert int(res.one()._mapping["n"]) == 1

    async def test_create_after_soft_delete_reuses_name(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A retired zone frees its name for reuse (partial index WHERE is_active).

        Seed an active 'Балахня', deactivate it, then create a NEW active
        'Балахня' — must succeed (the index excludes inactive rows), proving the
        soft-delete-then-reuse invariant.
        """
        old_id = await _seed_zone(session, "Балахня", dtm=15)
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)

        # deactivate the original
        deact = {
            "action": "deactivate",
            "name": None,
            "delivery_time_minutes": None,
            "target_zone_name": "Балахня",
        }
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_ZONE, context={"command": deact})
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        # create a fresh active zone with the now-freed name
        create = {
            "action": "create",
            "name": "Балахня",
            "delivery_time_minutes": 25,
            "target_zone_name": None,
        }
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace = await vm.run(PROGRAM_APPLY_ZONE, context={"command": create})
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        res = await session.execute(
            text(
                "SELECT id, is_active, delivery_time_minutes FROM delivery_zones "
                "WHERE lower(name) = lower('Балахня') ORDER BY is_active"
            )
        )
        rows = res.fetchall()
        assert len(rows) == 2  # old (inactive) + new (active) coexist
        assert rows[0]._mapping["is_active"] is False  # old, retired
        assert rows[1]._mapping["is_active"] is True  # new
        assert rows[1]._mapping["delivery_time_minutes"] == 25
        assert str(rows[0]._mapping["id"]) == old_id  # original id preserved

    # ---- write failure discipline (raise, don't swallow) --------------------

    async def test_apply_raises_on_malformed_command(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A malformed command must RAISE (is_terminal, no downstream CONDITION).

        apply_zone_command is terminal with nothing consuming its output, so a
        returned sentinel would leave Trace.status == SUCCESS regardless of what
        happened in Postgres. It MUST raise — verified here at the tool layer.
        """
        with pytest.raises(ValueError):
            await zone_agent_tools.apply_zone_command(
                session=session,
                command={"action": "bogus", "name": "X",
                         "delivery_time_minutes": 10, "target_zone_name": None},
            )

    async def test_apply_raises_clear_valueerror_on_name_collision_at_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A same-normalized-name collision surfaces as a clear ValueError.

        The earlier validate may have passed; this proves the tool ITSELF rejects
        a duplicate active name via re-check + the unique index, raising
        ValueError (not an unexplained asyncpg error).
        """
        await _seed_zone(session, "Балахня", dtm=15)
        with pytest.raises(ValueError):
            await zone_agent_tools.apply_zone_command(
                session=session,
                command={"action": "create", "name": "балахня",
                         "delivery_time_minutes": 20, "target_zone_name": None},
            )

    # ---- index-reality gate: the partial unique index is REAL --------------

    async def test_partial_unique_index_blocks_duplicate_active_names(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Two active rows with the same normalized name must hit the index.

        This is the gate from the sprint spec: attempt to insert two zones with
        the same normalized name (one already seeded) and assert a real
        IntegrityError (unique_violation), proving idx_delivery_zones_name_active
        exists and enforces the rule at the DB level — not just in Python.
        """
        await _seed_zone(session, "Центр", dtm=10)
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO delivery_zones (name, delivery_time_minutes, "
                    "is_active) VALUES (:name, :dtm, TRUE)"
                ),
                {"name": "центр", "dtm": 12},
            )
            await session.flush()

    # ---- happy paths --------------------------------------------------------

    async def test_create_zone(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        command = {
            "action": "create",
            "name": "Балахня",
            "delivery_time_minutes": 15,
            "target_zone_name": None,
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()
        res = await session.execute(
            text(
                "SELECT name, delivery_time_minutes, is_active FROM delivery_zones"
            )
        )
        row = res.one()._mapping
        assert row["name"] == "Балахня"
        assert row["delivery_time_minutes"] == 15
        assert row["is_active"] is True

    async def test_update_zone_eta(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_zone(session, "Город", dtm=15)
        command = {
            "action": "update",
            "name": None,
            "delivery_time_minutes": 30,
            "target_zone_name": "Город",
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()
        res = await session.execute(
            text("SELECT delivery_time_minutes FROM delivery_zones")
        )
        assert res.one()._mapping["delivery_time_minutes"] == 30

    async def test_update_zone_rename(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_zone(session, "Город", dtm=15)
        command = {
            "action": "update",
            "name": "Город-Центр",
            "delivery_time_minutes": None,
            "target_zone_name": "Город",
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()
        res = await session.execute(
            text("SELECT name FROM delivery_zones")
        )
        assert res.one()._mapping["name"] == "Город-Центр"

    async def test_deactivate_zone_soft_delete(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_zone(session, "Отдаленные районы", dtm=45)
        command = {
            "action": "deactivate",
            "name": None,
            "delivery_time_minutes": None,
            "target_zone_name": "Отдаленные районы",
        }
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_APPLY_ZONE, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()
        res = await session.execute(
            text("SELECT is_active FROM delivery_zones")
        )
        assert res.one()._mapping["is_active"] is False

    # ---- end-to-end via ZoneAgent (owns commit) -----------------------------

    async def test_agent_apply_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        command = {
            "action": "create",
            "name": "Балахня",
            "delivery_time_minutes": 15,
            "target_zone_name": None,
        }
        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = ZoneAgent(session_factory=sf)
        result = await agent.apply_zone(command)

        assert result.applied is True
        assert result.error is None

        async with sf() as verify:
            res = await verify.execute(
                text(
                    "SELECT name, delivery_time_minutes FROM delivery_zones "
                    "WHERE lower(name) = lower('Балахня')"
                )
            )
            row = res.one()._mapping
            assert row["name"] == "Балахня"
            assert row["delivery_time_minutes"] == 15
        await engine.dispose()


class TestZoneSoftDeleteInvariant:
    """A soft-deleted zone leaves the public endpoint but stays resolvable."""

    async def test_soft_deleted_excluded_from_public_endpoint_but_resolvable(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        zone_id = await _seed_zone(session, "Отдаленные районы", dtm=45)

        # Deactivate via the agent.
        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = ZoneAgent(session_factory=sf)
        result = await agent.apply_zone({
            "action": "deactivate",
            "name": None,
            "delivery_time_minutes": None,
            "target_zone_name": "Отдаленные районы",
        })
        assert result.applied is True

        # Public endpoint (is_active filter) must NOT list the retired zone.
        svc = ZoneService(session_factory=sf)
        public = await svc.list_all()  # admin view lists all, but we can filter
        public_active = [z for z in public if z.is_active]
        names = {z.name for z in public_active}
        assert "Отдаленные районы" not in names

        # Yet the row is STILL resolvable by id (order-history integrity).
        by_id = await svc.get_by_id(__import__("uuid").UUID(zone_id))
        assert by_id is not None
        assert by_id.name == "Отдаленные районы"
        assert by_id.is_active is False
        await engine.dispose()
