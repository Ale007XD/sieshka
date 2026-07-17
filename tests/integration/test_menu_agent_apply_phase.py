"""tests/integration/test_menu_agent_apply_phase.py — MenuAgent APPLY phase.

Requires Docker (sieshka-postgres). Skipped if unavailable.

This is the concrete "before" vs "after" for sprint_m7_agent_apply_phase_pattern:
before, menu_agent stopped at a terminal JSON command and wrote NOTHING; after,
apply_menu_command actually lands the confirmed command in Postgres — through the
SAME GovernedToolExecutor gate as every other write.

Deliberately failure-path-first (see the sprint's own note that pytest GREEN on a
validate-only unit test never meant "the feature works"):
  1. write fails → tool RAISES + Trace.status == FAILED, ZERO rows land.
  2. TOCTOU: a row inserted between validate and apply → apply re-checks at write
     time and RAISES rather than trusting the earlier validate.
  3. happy path → the command lands in Postgres.
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
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.menu_agent import (
    _APPLY_SESSION_TOOLS,
    _APPLY_TOOLS,
    MenuAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import MENU_AGENT_APPLY_POLICY_SNAPSHOT
from app.programs.menu_agent_program import PROGRAM_APPLY_MENU

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
        await s.execute(text("DELETE FROM products"))
        await s.execute(text("DELETE FROM categories"))
        await s.commit()
        yield s
    await engine.dispose()


async def _seed_category(session: AsyncSession, name: str) -> UUID:
    await session.execute(
        text(
            "INSERT INTO categories (name, menu_period, sort, is_active) "
            "VALUES (:name, 'both', 10, TRUE)"
        ),
        {"name": name},
    )
    await session.commit()
    res = await session.execute(
        text("SELECT id FROM categories WHERE name = :name"), {"name": name}
    )
    cid: UUID = res.one()._mapping["id"]
    return cid


def _build_apply_vm(
    session: AsyncSession,
    executor: GovernedToolExecutor,
    nano_store_path: str,
) -> ExecutionVM:
    """Mirror MenuAgent._build_apply_vm but with a spy-able executor + temp store."""
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


class TestMenuAgentApplyPhase:
    # ---- failure path FIRST -------------------------------------------------

    async def test_apply_raises_and_trace_failed_when_write_fails(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Category deleted between validate and apply → write FK failure.

        The category is resolved by validate_apply_command, then deleted from the
        same session before apply_command runs. apply_command re-resolves under
        FOR UPDATE and finds zero matching rows → raises → Trace.status FAILED.
        Nothing is committed, so ZERO product rows survive after rollback.
        """
        cat_id = await _seed_category(session, "Бургеры")

        command = {"name": "Vegan Burger", "category": "Бургеры", "price_rub": 350}

        executor = GovernedToolExecutor(policy=MENU_AGENT_APPLY_POLICY_SNAPSHOT)

        # Let validate pass, then delete the category right before the write runs
        # (a step hook wrapping the real apply tool). apply re-resolves the
        # category under FOR UPDATE, finds it gone, and raises → Trace FAILED.
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_menu_command

        async def _delete_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            await session.execute(
                text("DELETE FROM categories WHERE id = :cid"),
                {"cid": str(cat_id)},
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_APPLY_TOOLS)
        tools["apply_menu_command"] = _delete_then_apply
        from nano_vm_mcp.store import ProgramStore

        store = ProgramStore(nano_store_path)
        cursor = StoreCursorRepository(store)
        vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
        for name, fn in tools.items():
            governed = _governed_tool(fn, name, executor)
            if name in _APPLY_SESSION_TOOLS:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)

        trace: Trace = await vm.run(
            PROGRAM_APPLY_MENU, context={"command": command}
        )

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        res = await session.execute(text("SELECT COUNT(*) AS n FROM products"))
        assert int(res.one()._mapping["n"]) == 0

    async def test_apply_rechecks_name_at_write_time_toctou(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A product with the same name inserted between validate and apply.

        validate_apply_command sees the name free. Before apply writes, a
        concurrent insert lands the same name. apply_command RE-CHECKS
        name-not-in-use at write time and raises rather than creating a
        duplicate on the basis of the stale validate result.
        """
        await _seed_category(session, "Бургеры")
        command = {"name": "Combo", "category": "Бургеры", "price_rub": 700}

        executor = GovernedToolExecutor(policy=MENU_AGENT_APPLY_POLICY_SNAPSHOT)
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_menu_command

        async def _insert_dupe_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            # Simulate a concurrent second agent invocation that got there first.
            await session.execute(
                text(
                    "INSERT INTO products (name, price_rub, is_active) "
                    "VALUES ('Combo', 999, TRUE)"
                )
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_APPLY_TOOLS)
        tools["apply_menu_command"] = _insert_dupe_then_apply
        from nano_vm_mcp.store import ProgramStore

        store = ProgramStore(nano_store_path)
        cursor = StoreCursorRepository(store)
        vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
        for name, fn in tools.items():
            governed = _governed_tool(fn, name, executor)
            if name in _APPLY_SESSION_TOOLS:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)

        trace: Trace = await vm.run(PROGRAM_APPLY_MENU, context={"command": command})

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        # Only ever zero committed rows (the dupe insert was on the same,
        # rolled-back transaction). No second "Combo" leaked through.
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM products WHERE lower(name) = 'combo'")
        )
        assert int(res.one()._mapping["n"]) == 0

    async def test_invalid_command_reaches_invalid_terminal_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Unknown category → validate returns 0 → report_invalid terminal.

        This is a *valid* Trace (SUCCESS) that deliberately wrote nothing — the
        invalid branch is a consumed sentinel branch, not a raise.
        """
        await _seed_category(session, "Бургеры")
        command = {"name": "Ghost", "category": "NoSuchCat", "price_rub": 100}

        executor = GovernedToolExecutor(policy=MENU_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_MENU, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(text("SELECT COUNT(*) AS n FROM products"))
        assert int(res.one()._mapping["n"]) == 0

    # ---- happy path: the command actually lands in Postgres -----------------

    async def test_apply_lands_command_in_postgres_with_governance(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        cat_id = await _seed_category(session, "Бургеры")
        command = {
            "name": "Vegan Burger",
            "category": "Бургеры",
            "price_rub": 350,
            "description": "plant based",
        }

        executor = GovernedToolExecutor(policy=MENU_AGENT_APPLY_POLICY_SNAPSHOT)
        with patch.object(executor, "check", wraps=executor.check) as spy:
            vm = _build_apply_vm(session, executor, nano_store_path)
            trace: Trace = await vm.run(
                PROGRAM_APPLY_MENU, context={"command": command}
            )
            assert trace.status == TraceStatus.SUCCESS
            # Governance genuinely enforced on the write step.
            spy.assert_any_call("apply_menu_command")

        await session.commit()

        res = await session.execute(
            text(
                "SELECT name, price_rub, description, category_id, is_active "
                "FROM products WHERE lower(name) = 'vegan burger'"
            )
        )
        row = res.one()
        assert row._mapping["price_rub"] == 350
        assert row._mapping["description"] == "plant based"
        assert row._mapping["category_id"] == cat_id
        assert row._mapping["is_active"] is True

    async def test_agent_apply_menu_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        """MenuAgent.apply_menu owns commit — verify it persists across sessions."""
        await _seed_category(session, "Напитки")
        command = {"name": "Морс", "category": "Напитки", "price_rub": 120}

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = MenuAgent(session_factory=sf)

        result = await agent.apply_menu(command)

        assert result.applied is True
        assert result.error is None

        # Fresh session proves the commit really happened.
        async with sf() as verify:
            res = await verify.execute(
                text("SELECT price_rub FROM products WHERE name = :n"),
                {"n": "Морс"},
            )
            assert res.one()._mapping["price_rub"] == 120
        await engine.dispose()
