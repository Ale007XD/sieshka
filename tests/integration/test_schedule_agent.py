"""tests/integration/test_schedule_agent.py — ScheduleAgent APPLY phase.

Requires Docker (sieshka-postgres). Skipped if unavailable.

Failure-path-first (same discipline as test_menu_agent_apply_phase.py):
  1. invalid scope×action combo (reset_to_default + permanent) → rejected,
     nothing written (valid Trace, invalid terminal).
  2. write failure (unique_violation race) → tool RAISES + Trace FAILED.
  3. happy paths for every scope×action combination:
       permanent + set_hours
       today     + set_hours
       today     + reset_to_default
  4. expiration-by-read: a today-override row with effective_date = yesterday is
     NOT picked up by get_menu_window_context() — the only behavior with no code
     path that deletes anything; it works only if the read WHERE clause is right.
"""
from __future__ import annotations

import functools
import tempfile
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import datetime, time
from pathlib import Path

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import Trace, TraceStatus
from nano_vm.vm import ExecutionVM
from nano_vm_mcp.handlers import GovernedToolExecutor
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.schedule_agent import (
    _APPLY_SESSION_TOOLS,
    _APPLY_TOOLS,
    ScheduleAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT
from app.programs.schedule_agent_program import PROGRAM_APPLY_SCHEDULE
from app.services.schedule_service import ScheduleService

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
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as s:
        await s.execute(text("DELETE FROM schedule_windows"))
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


async def _seed_permanent(
    session: AsyncSession,
    morning: tuple[time, time] = (time(0, 0), time(16, 0)),
    evening: tuple[time, time] = (time(16, 0), time(23, 59, 59)),
) -> None:
    await session.execute(
        text(
            "INSERT INTO schedule_windows "
            "(period, start_time, end_time, is_active, effective_date) "
            "VALUES ('morning', :ms, :me, TRUE, NULL), "
            "('evening', :es, :ee, TRUE, NULL)"
        ),
        {
            "ms": morning[0], "me": morning[1],
            "es": evening[0], "ee": evening[1],
        },
    )
    await session.commit()


class TestScheduleAgentApplyPhase:
    # ---- invalid branch -----------------------------------------------------

    async def test_reset_permanent_rejected_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """reset_to_default + scope=permanent is rejected (no original config row).

        Valid Trace (SUCCESS) that reached the invalid terminal — nothing written.
        """
        await _seed_permanent(session)
        command = {
            "period": "morning",
            "action": "reset_to_default",
            "scope": "permanent",
            "start_time": None,
            "end_time": None,
        }

        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM schedule_windows")
        )
        # Still exactly the two seeded permanent rows — no mutation happened.
        assert int(res.one()._mapping["n"]) == 2

    async def test_set_hours_rejects_start_after_end(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_permanent(session)
        command = {
            "period": "morning",
            "action": "set_hours",
            "scope": "permanent",
            "start_time": "17:00",
            "end_time": "09:00",
        }
        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

    # ---- happy paths: every scope×action combination -------------------------

    async def test_permanent_set_hours_upserts(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_permanent(session)
        command = {
            "period": "morning",
            "action": "set_hours",
            "scope": "permanent",
            "start_time": "01:00",
            "end_time": "15:00",
        }
        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        res = await session.execute(
            text(
                "SELECT start_time, end_time FROM schedule_windows "
                "WHERE period='morning' AND effective_date IS NULL"
            )
        )
        row = res.one()
        assert row._mapping["start_time"] == time(1, 0)
        assert row._mapping["end_time"] == time(15, 0)
        # Exactly one permanent morning row (upsert, not a second insert).
        res2 = await session.execute(
            text(
                "SELECT COUNT(*) AS n FROM schedule_windows "
                "WHERE period='morning' AND effective_date IS NULL"
            )
        )
        assert int(res2.one()._mapping["n"]) == 1

    async def test_today_set_hours_upserts_without_touching_permanent(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_permanent(session)
        command = {
            "period": "evening",
            "action": "set_hours",
            "scope": "today",
            "start_time": "00:00",
            "end_time": "20:00",
        }
        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        # Permanent evening row untouched.
        res = await session.execute(
            text(
                "SELECT end_time FROM schedule_windows "
                "WHERE period='evening' AND effective_date IS NULL"
            )
        )
        assert res.one()._mapping["end_time"] == time(23, 59, 59)
        # A today override row exists for today and is preferred on read.
        svc = ScheduleService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]
        windows = await svc._load_windows()
        assert windows["evening"].end_time == time(20, 0)

    async def test_today_reset_to_default_deletes_override(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_permanent(session)
        # Seed a today override directly.
        await session.execute(
            text(
                "INSERT INTO schedule_windows "
                "(period, start_time, end_time, is_active, effective_date) "
                "VALUES ('evening', '00:00', '20:00', TRUE, CURRENT_DATE)"
            )
        )
        await session.commit()

        command = {
            "period": "evening",
            "action": "reset_to_default",
            "scope": "today",
            "start_time": None,
            "end_time": None,
        }
        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        res = await session.execute(
            text(
                "SELECT COUNT(*) AS n FROM schedule_windows "
                "WHERE period='evening' AND effective_date = CURRENT_DATE"
            )
        )
        assert int(res.one()._mapping["n"]) == 0
        # Permanent evening row still present (fall-through on read).
        svc = ScheduleService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]
        windows = await svc._load_windows()
        assert windows["evening"].end_time == time(23, 59, 59)

    async def test_today_reset_idempotent_when_no_override(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        await _seed_permanent(session)  # no today override for morning
        command = {
            "period": "morning",
            "action": "reset_to_default",
            "scope": "today",
            "start_time": None,
            "end_time": None,
        }
        executor = GovernedToolExecutor(policy=SCHEDULE_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_SCHEDULE, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        await session.commit()
        res = await session.execute(text("SELECT COUNT(*) AS n FROM schedule_windows"))
        assert int(res.one()._mapping["n"]) == 2  # nothing deleted

    # ---- write failure discipline (raise, don't swallow) --------------------

    async def test_apply_raises_clear_valueerror_on_malformed_command(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A malformed command must RAISE (is_terminal, no downstream CONDITION).

        apply_schedule_command is terminal with nothing consuming its output, so
        a returned sentinel would leave Trace.status == SUCCESS regardless of what
        happened in Postgres. It MUST raise — verified here at the tool layer.
        """
        from app.tools import schedule_agent_tools

        with pytest.raises(ValueError):
            await schedule_agent_tools.apply_schedule_command(
                session=session,
                command={"period": "bogus", "action": "set_hours",
                         "scope": "today", "start_time": "09:00",
                         "end_time": "10:00"},
            )

    async def test_apply_raises_on_invalid_times_at_write_time(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """set_hours with start >= end re-checked at write time → raises.

        The earlier validate is an early-rejection convenience, not enforcement;
        this proves the tool itself rejects a bad time ordering rather than
        inserting it (which would corrupt the window).
        """
        from app.tools import schedule_agent_tools

        with pytest.raises(ValueError):
            await schedule_agent_tools.apply_schedule_command(
                session=session,
                command={"period": "morning", "action": "set_hours",
                         "scope": "permanent", "start_time": "17:00",
                         "end_time": "09:00"},
            )

    # ---- expiration-by-read (no deletion path) -----------------------------

    async def test_yesterday_override_expires_by_read_fallthrough(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Insert a today-override with effective_date = YESTERDAY directly.

        get_menu_window_context()/_load_windows must NOT pick it up and must
        fall through to the permanent row. This is the one behavior with no code
        path that deletes anything — it only works if the WHERE clause is right.
        """
        from datetime import timedelta

        from app.services.schedule_service import _menu_timezone

        await _seed_permanent(session)
        yesterday = (datetime.now(_menu_timezone()).date() - timedelta(days=1))
        await session.execute(
            text(
                "INSERT INTO schedule_windows "
                "(period, start_time, end_time, is_active, effective_date) "
                "VALUES ('morning', '08:00', '12:00', TRUE, :y)"
            ),
            {"y": yesterday},
        )
        await session.commit()

        svc = ScheduleService()
        svc._session_factory = lambda: _asession(session)  # type: ignore[assignment]
        windows = await svc._load_windows()

        # Permanent morning row wins; the stale override is invisible.
        assert windows["morning"].start_time == time(0, 0)
        assert windows["morning"].end_time == time(16, 0)

    # ---- end-to-end via ScheduleAgent (owns commit) -------------------------

    async def test_agent_apply_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        await _seed_permanent(session)
        command = {
            "period": "morning",
            "action": "set_hours",
            "scope": "permanent",
            "start_time": "01:00",
            "end_time": "15:00",
        }

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = ScheduleAgent(session_factory=sf)
        result = await agent.apply_schedule(command)

        assert result.applied is True
        assert result.error is None

        async with sf() as verify:
            res = await verify.execute(
                text(
                    "SELECT start_time FROM schedule_windows "
                    "WHERE period='morning' AND effective_date IS NULL"
                )
            )
            assert res.one()._mapping["start_time"] == time(1, 0)
        await engine.dispose()


@asynccontextmanager
async def _asession(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    yield session
