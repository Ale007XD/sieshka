"""tests/integration/test_menu_agent_update_product_phase.py — MenuAgent
UPDATE-PRODUCT phase (sprint_menu_product_reorder, 2026-08-17).

Requires Docker (sieshka-postgres). Skipped if unavailable.

Mirrors test_menu_agent_update_category_phase.py's structure for the product
UPDATE path (validate_update_product_command → CONDITION →
apply_update_product_command | report_invalid_update_product_command).
Failure-path-first, same rationale: pytest GREEN on a validate-only unit
test never meant "the feature works" — this exercises the real TOCTOU
re-checks and the governed write itself.

  1. write fails → tool RAISES + Trace.status == FAILED, nothing committed.
  2. TOCTOU (sku claimed by another product between validate and apply) →
     apply re-checks at write time and RAISES rather than trusting the
     earlier validate.
  3. invalid command (unknown category) → clean invalid terminal, no write,
     Trace stays SUCCESS.
  4. happy path, including the new `sort` field → the update actually lands
     in Postgres, governance enforced.
  5. partial update (sort only) → other fields left unchanged (COALESCE).

SCHEMA NOTE: unlike test_menu_agent_apply_phase.py and
test_menu_agent_update_category_phase.py, _seed_category below seeds
`time_period` + `fulfillment_scope` (current schema, migrations/
014_menu_period_split.sql) rather than the old single `menu_period` column
those two sibling files still use. `menu_period` was DROPPED in migration
014 — seeding it would raise UndefinedColumnError against a real Postgres
instance. Flagged for a separate fix; out of scope for this delivery (this
repo's CI gate runs `pytest tests/unit/` only — tests/integration/ isn't
invoked by GitHub CI at all, so that pre-existing bug hasn't surfaced there;
see .github/workflows/ci.yml's `test` job).
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
    _UPDATE_PRODUCT_SESSION_TOOLS,
    _UPDATE_PRODUCT_TOOLS,
    MenuAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT
from app.programs.menu_agent_program import PROGRAM_UPDATE_PRODUCT

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
            "INSERT INTO categories (name, time_period, fulfillment_scope, sort, is_active) "
            "VALUES (:name, 'both', 'both', 10, TRUE)"
        ),
        {"name": name},
    )
    await session.commit()
    res = await session.execute(
        text("SELECT id FROM categories WHERE name = :name"), {"name": name}
    )
    cid: UUID = res.one()._mapping["id"]
    return cid


async def _seed_product(
    session: AsyncSession,
    name: str,
    category_id: UUID,
    price_rub: int = 100,
    sort: int = 0,
    sku: str | None = None,
) -> UUID:
    await session.execute(
        text(
            "INSERT INTO products (name, category_id, price_rub, sort, sku, is_active) "
            "VALUES (:name, :category_id, :price_rub, :sort, :sku, TRUE)"
        ),
        {
            "name": name,
            "category_id": category_id,
            "price_rub": price_rub,
            "sort": sort,
            "sku": sku,
        },
    )
    await session.commit()
    res = await session.execute(
        text("SELECT id FROM products WHERE name = :name"), {"name": name}
    )
    pid: UUID = res.one()._mapping["id"]
    return pid


def _build_update_product_vm(
    session: AsyncSession,
    executor: GovernedToolExecutor,
    nano_store_path: str,
    tools: dict[str, object] | None = None,
) -> ExecutionVM:
    """Mirror MenuAgent._build_generic_apply_vm (update_product branch) but
    with a spy-able executor + temp store, and an optional tools override
    for TOCTOU-hook tests."""
    from nano_vm_mcp.store import ProgramStore

    store = ProgramStore(nano_store_path)
    cursor = StoreCursorRepository(store)
    vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
    tool_map = tools if tools is not None else _UPDATE_PRODUCT_TOOLS
    for name, fn in tool_map.items():
        governed = _governed_tool(fn, name, executor)
        if name in _UPDATE_PRODUCT_SESSION_TOOLS:
            vm.register_tool(name, functools.partial(governed, session=session))
        else:
            vm.register_tool(name, governed)
    return vm


class TestMenuAgentUpdateProductPhase:
    # ---- failure path FIRST -------------------------------------------------

    async def test_apply_raises_and_trace_failed_when_product_deleted(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Product deleted between validate and apply → FOR UPDATE finds 0 rows.

        validate_update_product_command resolves the product fine. Before
        apply_update_product_command runs, the row is deleted from the same
        session. apply re-resolves under FOR UPDATE, finds it gone, and
        raises → Trace.status FAILED. Nothing else in the row survives the
        rollback.
        """
        cat_id = await _seed_category(session, "Бургеры")
        prod_id = await _seed_product(session, "Классик Бургер", cat_id)
        command = {"product_id": str(prod_id), "price_rub": 250}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT)
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_update_product_command

        async def _delete_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            await session.execute(
                text("DELETE FROM products WHERE id = :pid"),
                {"pid": str(prod_id)},
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_UPDATE_PRODUCT_TOOLS)
        tools["apply_update_product_command"] = _delete_then_apply
        vm = _build_update_product_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(
            PROGRAM_UPDATE_PRODUCT, context={"command": command}
        )

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        # rollback() undoes BOTH the in-flight DELETE and the would-be
        # UPDATE — the row still exists (delete never committed), so the
        # real assertion is that the *intended write* (price_rub=250) never
        # landed, not that the row is gone (mirrors
        # test_apply_raises_and_trace_failed_when_category_deleted, which
        # checks the target *name* never landed, not category non-existence).
        res = await session.execute(
            text("SELECT price_rub FROM products WHERE id = :id"), {"id": prod_id}
        )
        assert res.one()._mapping["price_rub"] == 100  # unchanged from seed

    async def test_apply_rechecks_sku_at_write_time_toctou(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A different product claims the target sku between validate and
        apply — apply RE-CHECKS sku-not-in-use (excluding own id) at write
        time and raises rather than creating a duplicate sku (also a DB-level
        UNIQUE constraint, migrations/015_products_sku.sql — this proves the
        clean ValueError fires before that constraint would).
        """
        cat_id = await _seed_category(session, "Бургеры")
        prod_id = await _seed_product(session, "Классик Бургер", cat_id)
        command = {"product_id": str(prod_id), "sku": "PREMIUM-BURGER"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT)
        from app.tools import menu_agent_tools

        real_apply = menu_agent_tools.apply_update_product_command

        async def _claim_sku_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            # Simulate a concurrent second agent that already claimed this sku.
            await session.execute(
                text(
                    "INSERT INTO products (name, category_id, price_rub, sku, is_active) "
                    "VALUES ('Премиум Бургер', :cid, 300, 'PREMIUM-BURGER', TRUE)"
                ),
                {"cid": cat_id},
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_UPDATE_PRODUCT_TOOLS)
        tools["apply_update_product_command"] = _claim_sku_then_apply
        vm = _build_update_product_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(
            PROGRAM_UPDATE_PRODUCT, context={"command": command}
        )

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        # Original product's sku must remain untouched (rollback), and only
        # the one pre-existing "Премиум Бургер" row claims the sku — no
        # accidental second one.
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM products WHERE sku = 'PREMIUM-BURGER'")
        )
        assert int(res.one()._mapping["n"]) == 0  # rolled back along with the concurrent insert
        res2 = await session.execute(
            text("SELECT sku FROM products WHERE id = :id"), {"id": prod_id}
        )
        assert res2.one()._mapping["sku"] is None

    # ---- invalid commands: clean rejection, no write -------------------------

    async def test_unknown_category_reaches_invalid_terminal_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Unknown category → validate returns 0 → report_invalid terminal.

        Valid Trace (SUCCESS) that deliberately wrote nothing — invalid
        branch is a consumed sentinel branch, not a raise.
        """
        cat_id = await _seed_category(session, "Бургеры")
        prod_id = await _seed_product(session, "Классик Бургер", cat_id)
        command = {"product_id": str(prod_id), "category": "NoSuchCategory"}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT)
        vm = _build_update_product_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_UPDATE_PRODUCT, context={"command": command}
        )

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT category_id FROM products WHERE id = :id"), {"id": prod_id},
        )
        assert res.one()._mapping["category_id"] == cat_id  # unchanged

    # ---- happy path: the update actually lands in Postgres -------------------

    async def test_apply_updates_row_in_postgres_with_governance(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        cat_id = await _seed_category(session, "Бургеры")
        new_cat_id = await _seed_category(session, "Комбо")
        prod_id = await _seed_product(session, "Классик Бургер", cat_id, price_rub=200, sort=0)
        command = {
            "product_id": str(prod_id),
            "name": "Классик Бургер Премиум",
            "category": "Комбо",
            "price_rub": 350,
            "sort": 20,
            "is_active": False,
        }

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT)
        with patch.object(executor, "check", wraps=executor.check) as spy:
            vm = _build_update_product_vm(session, executor, nano_store_path)
            trace: Trace = await vm.run(
                PROGRAM_UPDATE_PRODUCT, context={"command": command}
            )
            assert trace.status == TraceStatus.SUCCESS
            spy.assert_any_call("apply_update_product_command")

        await session.commit()

        res = await session.execute(
            text(
                "SELECT name, category_id, price_rub, sort, is_active "
                "FROM products WHERE id = :id"
            ),
            {"id": prod_id},
        )
        row = res.one()
        assert row._mapping["name"] == "Классик Бургер Премиум"
        assert row._mapping["category_id"] == new_cat_id
        assert row._mapping["price_rub"] == 350
        assert row._mapping["sort"] == 20
        assert row._mapping["is_active"] is False

    async def test_partial_update_sort_only_leaves_other_fields_unchanged(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Only sort provided → name/price_rub/is_active stay as seeded
        (COALESCE semantics — absent fields are None, not a reset to
        default). This is the exact shape the admin reorder arrows send
        (menu_admin.html::moveProduct — PATCH body is `{sort: N}` only)."""
        cat_id = await _seed_category(session, "Бургеры")
        prod_id = await _seed_product(session, "Классик Бургер", cat_id, price_rub=200, sort=0)
        command = {"product_id": str(prod_id), "sort": 30}

        executor = GovernedToolExecutor(policy=MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT)
        vm = _build_update_product_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(
            PROGRAM_UPDATE_PRODUCT, context={"command": command}
        )
        assert trace.status == TraceStatus.SUCCESS

        await session.commit()
        res = await session.execute(
            text(
                "SELECT name, price_rub, sort, is_active FROM products WHERE id = :id"
            ),
            {"id": prod_id},
        )
        row = res.one()
        assert row._mapping["name"] == "Классик Бургер"  # unchanged
        assert row._mapping["price_rub"] == 200  # unchanged
        assert row._mapping["sort"] == 30
        assert row._mapping["is_active"] is True  # unchanged

    async def test_agent_update_product_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        """MenuAgent.update_product owns commit — verify it persists across
        sessions, not just within the fixture's own transaction."""
        cat_id = await _seed_category(session, "Напитки Тест")
        prod_id = await _seed_product(session, "Кола Тест", cat_id)
        command = {"product_id": str(prod_id), "sort": 99}

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = MenuAgent(session_factory=sf)

        result = await agent.update_product(command)

        assert result.applied is True
        assert result.error is None

        async with sf() as verify:
            res = await verify.execute(
                text("SELECT sort FROM products WHERE id = :id"), {"id": prod_id},
            )
            assert res.one()._mapping["sort"] == 99
        await engine.dispose()
