"""tests/integration/test_menu_agent_update_category_phase.py — MenuAgent UPDATE-CATEGORY phase.

Requires Docker (sieshka-postgres). Skipped if unavailable.

Mirrors test_menu_agent_apply_phase.py's structure for the category UPDATE path
(validate_update_category_command → CONDITION → apply_update_category_command |
report_invalid_update_category_command). Failure-path-first, same rationale:
pytest GREEN on a validate-only unit test never meant "the feature works" —
this exercises the real TOCTOU re-checks and the governed write itself.

  1. write fails → tool RAISES + Trace.status == FAILED, nothing committed.
  2. TOCTOU (name collision inserted between validate and apply) → apply
     re-checks at write time and RAISES rather than trusting stale validate.
  3. invalid commands (unknown parent / self-parent) → clean invalid terminal,
     no write, Trace stays SUCCESS.
  4. happy path → the update actually lands in Postgres, governance enforced.
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
    _UPDATE_CATEGORY_SESSION_TOOLS,
    _UPDATE_CATEGORY_TOOLS,
    MenuAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT
from app.programs.menu_agent_program import PROGRAM_UPDATE_CATEGORY

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


async def _seed_category(
    session: AsyncSession, name: str, parent_id: UUID | None = None
) -> UUID:
    await session.execute(
        text(
            "INSERT INTO categories "
            "(name, parent_category_id, time_period, fulfillment_scope, sort, is_active) "
            "VALUES (:name, :parent_id, 'both', 'both', 10, TRUE)"
        ),
        {"name": name, "parent_id": parent_id},
    )
    await session.commit()
    res = await session.execute(
        text("SELECT id FROM categories WHERE name = :name"), {"name": name}
    )
    cid: UUID = res.one()._mapping["id"]
    return cid


def _build_update_category_vm(
    session: AsyncSession,
    executor: GovernedToolExecutor,
    nano_store_path: str,
    tools: dict[str, object] | None = None,
) -> ExecutionVM:
    """Mirror MenuAgent._build_update_category_vm but with a spy-able executor
    + temp store, and an optional tools override for TOCTOU-hook tests."""
    from nano_vm_mcp.store import ProgramStore

    store = ProgramStore(nano_store_path)
    cursor = StoreCursorRepository(store)
    vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
    tool_map = tools if tools is not None else _UPDATE_CATEGORY_TOOLS
    for name, fn in tool_map.items():
        governed = _governed_tool(fn, name, executor)
        if name in _UPDATE_CATEGORY_SESSION_TOOLS:
            vm.register_tool(name, functools.partial(governed, session=session))
        else:
            vm.register_tool(name, governed)
    return vm


class TestMenuAgentUpdateCategoryPhase:
    # ---- failure path FIRST -------------------------------------------------

    async def test_apply_raises_and_trace_failed_when_category_deleted(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Category deleted between validate and apply → FOR UPDATE finds 0 rows.

        validate_update_category_command resolves the category fine. Before
        apply_update_category_command runs, the row is deleted from the same
        session. apply re-resolves under FOR UPDATE, finds it gone, and raises
        → Trace.status FAILED. Nothing else in the row survives the rollback.
        """
        cat_id = await _seed_category(session, "Бургеры")
        command = {"category_id": str(cat_id), "name": "Бургеры Премиум"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_update_category_command

        async def _delete_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            await session.execute(
                text("DELETE FROM categories WHERE id = :cid"),
                {"cid": str(cat_id)},
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_UPDATE_CATEGORY_TOOLS)
        tools["apply_update_category_command"] = _delete_then_apply
        vm = _build_update_category_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(
            PROGRAM_UPDATE_CATEGORY, context={"command": command}
        )

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM categories WHERE name = 'Бургеры Премиум'")
        )
        assert int(res.one()._mapping["n"]) == 0

    async def test_apply_rechecks_name_at_write_time_toctou(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A different category renamed to the target name between validate
        and apply — apply RE-CHECKS name-not-in-use (excluding own id) at
        write time and raises rather than creating a duplicate name.
        """
        cat_id = await _seed_category(session, "Бургеры")
        command = {"category_id": str(cat_id), "name": "Напитки"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_update_category_command

        async def _insert_dupe_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            # Simulate a concurrent second agent that already claimed this name.
            await session.execute(
                text(
                    "INSERT INTO categories "
                    "(name, time_period, fulfillment_scope, sort, is_active) "
                    "VALUES ('Напитки', 'both', 'both', 5, TRUE)"
                )
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_UPDATE_CATEGORY_TOOLS)
        tools["apply_update_category_command"] = _insert_dupe_then_apply
        vm = _build_update_category_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(
            PROGRAM_UPDATE_CATEGORY, context={"command": command}
        )

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        # Original category's name must remain untouched (rollback), and only
        # the one pre-existing "Напитки" row exists — no accidental second one.
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM categories WHERE name = 'Напитки'")
        )
        assert int(res.one()._mapping["n"]) == 0  # rolled back along with the dupe insert
        res2 = await session.execute(
            text("SELECT name FROM categories WHERE id = :id"), {"id": cat_id}
        )
        assert res2.one()._mapping["name"] == "Бургеры"

    # ---- invalid commands: clean rejection, no write -------------------------

    async def test_unknown_parent_reaches_invalid_terminal_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Unknown parent_category → validate returns 0 → report_invalid terminal.

        Valid Trace (SUCCESS) that deliberately wrote nothing — invalid branch
        is a consumed sentinel branch, not a raise.
        """
        cat_id = await _seed_category(session, "Бургеры")
        command = {"category_id": str(cat_id), "parent_category": "NoSuchParent"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        vm = _build_update_category_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_UPDATE_CATEGORY, context={"command": command}
        )

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT parent_category_id FROM categories WHERE id = :id"),
            {"id": cat_id},
        )
        assert res.one()._mapping["parent_category_id"] is None

    async def test_self_parent_rejected_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A category cannot become its own parent — validate returns 0."""
        cat_id = await _seed_category(session, "Бургеры")
        command = {"category_id": str(cat_id), "parent_category": "Бургеры"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        vm = _build_update_category_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_UPDATE_CATEGORY, context={"command": command}
        )

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT parent_category_id FROM categories WHERE id = :id"),
            {"id": cat_id},
        )
        assert res.one()._mapping["parent_category_id"] is None

    # ---- happy path: the update actually lands in Postgres -------------------

    async def test_apply_updates_row_in_postgres_with_governance(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        parent_id = await _seed_category(session, "Еда")
        cat_id = await _seed_category(session, "Бургеры")
        command = {
            "category_id": str(cat_id),
            "name": "Бургеры Премиум",
            "parent_category": "Еда",
            "fulfillment_scope": "delivery",
            "sort": 3,
            "is_active": False,
        }

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        with patch.object(executor, "check", wraps=executor.check) as spy:
            vm = _build_update_category_vm(session, executor, nano_store_path)
            trace: Trace = await vm.run(
                PROGRAM_UPDATE_CATEGORY, context={"command": command}
            )
            assert trace.status == TraceStatus.SUCCESS
            spy.assert_any_call("apply_update_category_command")

        await session.commit()

        res = await session.execute(
            text(
                "SELECT name, parent_category_id, fulfillment_scope, sort, is_active "
                "FROM categories WHERE id = :id"
            ),
            {"id": cat_id},
        )
        row = res.one()
        assert row._mapping["name"] == "Бургеры Премиум"
        assert row._mapping["parent_category_id"] == parent_id
        assert row._mapping["fulfillment_scope"] == "delivery"
        assert row._mapping["sort"] == 3
        assert row._mapping["is_active"] is False

    async def test_partial_update_leaves_absent_fields_unchanged(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Only name provided → time_period/fulfillment_scope/sort/is_active
        stay as seeded (COALESCE semantics — absent fields are None, not a
        reset to default)."""
        cat_id = await _seed_category(session, "Бургеры")
        command = {"category_id": str(cat_id), "name": "Бургеры V2"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_CATEGORY_POLICY_SNAPSHOT)
        vm = _build_update_category_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_UPDATE_CATEGORY, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS

        await session.commit()
        res = await session.execute(
            text(
                "SELECT name, time_period, fulfillment_scope, sort, is_active "
                "FROM categories WHERE id = :id"
            ),
            {"id": cat_id},
        )
        row = res.one()
        assert row._mapping["name"] == "Бургеры V2"
        assert row._mapping["time_period"] == "both"  # unchanged from seed
        assert row._mapping["fulfillment_scope"] == "both"  # unchanged from seed
        assert row._mapping["sort"] == 10  # unchanged from seed
        assert row._mapping["is_active"] is True  # unchanged from seed

    async def test_agent_update_category_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        """MenuAgent.update_category owns commit — verify it persists across
        sessions, not just within the fixture's own transaction."""
        cat_id = await _seed_category(session, "Напитки Тест")
        command = {"category_id": str(cat_id), "sort": 99}

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = MenuAgent(session_factory=sf)

        result = await agent.update_category(command)

        assert result.applied is True
        assert result.error is None

        async with sf() as verify:
            res = await verify.execute(
                text("SELECT sort FROM categories WHERE id = :id"), {"id": cat_id},
            )
            assert res.one()._mapping["sort"] == 99
        await engine.dispose()