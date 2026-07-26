"""tests/integration/test_promotion_agent_apply_phase.py — PromotionAgent APPLY phase.

Requires Docker (sieshka-postgres). Skipped if unavailable.

Mirrors test_menu_agent_apply_phase.py / test_menu_agent_update_category_phase.py's
structure for promotions. Two distinct write shapes share one apply tool:
  - action="create": INSERT a new promotion (state defaults to CREATED)
  - action="activate"|"expire"|"archive": FSM transition on an EXISTING promotion,
    validated against PROMOTION_TRANSITIONS using the CURRENT state under lock —
    same race-guard discipline as order_tools.py::transition_order_state.

Failure-path-first, same rationale as the sibling apply-phase test files:
  1. create: name collision inserted between validate and apply -> RAISES.
  2. transition: promotion's state changed concurrently between validate and
     apply -> apply re-resolves under FOR UPDATE and RAISES rather than
     trusting the stale validate result.
  3. invalid commands (unknown target / disallowed transition) -> clean
     invalid terminal, no write, Trace stays SUCCESS.
  4. happy paths -> create lands in Postgres; transition actually changes state.
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

from app.agents.promotion_agent import (
    _APPLY_SESSION_TOOLS,
    _APPLY_TOOLS,
    PromotionAgent,
    _governed_tool,
)
from app.db_nano import StoreCursorRepository
from app.policy.policy_snapshot import PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT
from app.programs.promotion_agent_program import PROGRAM_APPLY_PROMOTION

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
        await s.execute(text("DELETE FROM promotions"))
        await s.commit()
        yield s
    await engine.dispose()


async def _seed_promotion(
    session: AsyncSession, name: str, discount: float = 10.0, state: str = "CREATED"
) -> UUID:
    await session.execute(
        text(
            "INSERT INTO promotions (name, discount, state) "
            "VALUES (:name, :discount, :state)"
        ),
        {"name": name, "discount": discount, "state": state},
    )
    await session.commit()
    res = await session.execute(
        text("SELECT id FROM promotions WHERE name = :name"), {"name": name}
    )
    pid: UUID = res.one()._mapping["id"]
    return pid


def _build_apply_vm(
    session: AsyncSession,
    executor: GovernedToolExecutor,
    nano_store_path: str,
    tools: dict[str, object] | None = None,
) -> ExecutionVM:
    """Mirror PromotionAgent._build_apply_vm but with a spy-able executor +
    temp store, and an optional tools override for TOCTOU-hook tests."""
    from nano_vm_mcp.store import ProgramStore

    store = ProgramStore(nano_store_path)
    cursor = StoreCursorRepository(store)
    vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
    tool_map = tools if tools is not None else _APPLY_TOOLS
    for name, fn in tool_map.items():
        governed = _governed_tool(fn, name, executor)
        if name in _APPLY_SESSION_TOOLS:
            vm.register_tool(name, functools.partial(governed, session=session))
        else:
            vm.register_tool(name, governed)
    return vm


class TestPromotionAgentApplyPhase:
    # ---- failure path FIRST: create ------------------------------------------

    async def test_apply_rechecks_name_at_write_time_toctou_on_create(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """A promotion with the same name inserted between validate and apply.

        validate_apply_promotion_command sees the name free. Before apply
        writes, a concurrent insert lands the same name. apply RE-CHECKS
        name-not-in-use at write time (FOR UPDATE) and raises rather than
        creating a duplicate on the basis of the stale validate result.
        """
        command = {
            "action": "create", "name": "Летняя", "discount": 20,
            "target_promotion_name": None,
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        from app.tools import promotion_agent_tools

        real_apply = promotion_agent_tools.apply_promotion_command

        async def _insert_dupe_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            await session.execute(
                text(
                    "INSERT INTO promotions (name, discount, state) "
                    "VALUES ('Летняя', 99, 'CREATED')"
                )
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_APPLY_TOOLS)
        tools["apply_promotion_command"] = _insert_dupe_then_apply
        vm = _build_apply_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        res = await session.execute(
            text("SELECT COUNT(*) AS n FROM promotions WHERE lower(name) = 'летняя'")
        )
        assert int(res.one()._mapping["n"]) == 0

    # ---- failure path FIRST: transition ---------------------------------------

    async def test_apply_rechecks_state_at_write_time_toctou_on_transition(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """Promotion's state changes concurrently between validate and apply.

        validate_apply_promotion_command sees CREATED (activate is legal).
        Before apply's own FOR UPDATE re-read, a concurrent transaction moves
        the promotion straight to ARCHIVED. apply re-resolves the CURRENT
        state under lock, finds (ARCHIVED, ACTIVATE) not in
        PROMOTION_TRANSITIONS, and raises rather than trusting the stale
        validate result.
        """
        promo_id = await _seed_promotion(session, "Осенняя", state="CREATED")
        command = {
            "action": "activate", "name": None, "discount": None,
            "target_promotion_name": "Осенняя",
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        from app.tools import promotion_agent_tools

        real_apply = promotion_agent_tools.apply_promotion_command

        async def _archive_then_apply(
            session: AsyncSession, command: object, **kwargs: object
        ) -> object:
            await session.execute(
                text("UPDATE promotions SET state = 'ARCHIVED' WHERE id = :id"),
                {"id": promo_id},
            )
            return await real_apply(session=session, command=command, **kwargs)

        tools = dict(_APPLY_TOOLS)
        tools["apply_promotion_command"] = _archive_then_apply
        vm = _build_apply_vm(session, executor, nano_store_path, tools)

        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        assert trace.status == TraceStatus.FAILED

        await session.rollback()
        res = await session.execute(
            text("SELECT state FROM promotions WHERE id = :id"), {"id": promo_id}
        )
        # Rolled back along with the concurrent UPDATE — back to the seeded state.
        assert res.one()._mapping["state"] == "CREATED"

    # ---- invalid commands: clean rejection, no write ---------------------------

    async def test_disallowed_transition_reaches_invalid_terminal_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """archive from CREATED is not in PROMOTION_TRANSITIONS -> validate
        returns 0 -> report_invalid terminal. Valid Trace (SUCCESS) that
        deliberately wrote nothing."""
        promo_id = await _seed_promotion(session, "Зимняя", state="CREATED")
        command = {
            "action": "archive", "name": None, "discount": None,
            "target_promotion_name": "Зимняя",
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(
            text("SELECT state FROM promotions WHERE id = :id"), {"id": promo_id}
        )
        assert res.one()._mapping["state"] == "CREATED"

    async def test_unknown_target_reaches_invalid_terminal_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """target_promotion_name that doesn't exist -> validate returns 0."""
        command = {
            "action": "activate", "name": None, "discount": None,
            "target_promotion_name": "NoSuchPromo",
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(text("SELECT COUNT(*) AS n FROM promotions"))
        assert int(res.one()._mapping["n"]) == 0

    async def test_discount_out_of_range_rejected_no_write(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """discount > 100 -> validate returns 0, nothing created."""
        command = {
            "action": "create", "name": "Слишком щедрая", "discount": 150,
            "target_promotion_name": None,
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        assert trace.status == TraceStatus.SUCCESS
        invalid = next(s for s in trace.steps if s.step_id == "report_invalid")
        assert str(invalid.output).startswith("INVALID:")

        await session.commit()
        res = await session.execute(text("SELECT COUNT(*) AS n FROM promotions"))
        assert int(res.one()._mapping["n"]) == 0

    # ---- happy paths: the write actually lands in Postgres ---------------------

    async def test_apply_creates_promotion_in_postgres_with_governance(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        command = {
            "action": "create", "name": "Весенняя", "discount": 15,
            "target_promotion_name": None,
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        with patch.object(executor, "check", wraps=executor.check) as spy:
            vm = _build_apply_vm(session, executor, nano_store_path)
            trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})
            assert trace.status == TraceStatus.SUCCESS
            spy.assert_any_call("apply_promotion_command")

        await session.commit()

        res = await session.execute(
            text("SELECT name, discount, state FROM promotions WHERE lower(name) = 'весенняя'")
        )
        row = res.one()
        assert float(row._mapping["discount"]) == 15.0
        assert row._mapping["state"] == "CREATED"

    async def test_apply_transitions_promotion_state_in_postgres(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        promo_id = await _seed_promotion(session, "Праздничная", state="CREATED")
        command = {
            "action": "activate", "name": None, "discount": None,
            "target_promotion_name": "Праздничная",
        }

        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace: Trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})
        assert trace.status == TraceStatus.SUCCESS

        await session.commit()
        res = await session.execute(
            text("SELECT state FROM promotions WHERE id = :id"), {"id": promo_id}
        )
        assert res.one()._mapping["state"] == "ACTIVE"

    async def test_full_lifecycle_create_activate_expire_archive(
        self, session: AsyncSession, nano_store_path: str,
    ) -> None:
        """End-to-end walk through the whole FSM, one governed apply call per hop."""
        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)

        create_cmd = {
            "action": "create", "name": "ФуллЦикл", "discount": 25,
            "target_promotion_name": None,
        }
        vm = _build_apply_vm(session, executor, nano_store_path)
        trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": create_cmd})
        assert trace.status == TraceStatus.SUCCESS
        await session.commit()

        for action, expected_state in (
            ("activate", "ACTIVE"),
            ("expire", "EXPIRED"),
            ("archive", "ARCHIVED"),
        ):
            cmd = {
                "action": action, "name": None, "discount": None,
                "target_promotion_name": "ФуллЦикл",
            }
            vm = _build_apply_vm(session, executor, nano_store_path)
            trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": cmd})
            assert trace.status == TraceStatus.SUCCESS, f"failed at action={action}"
            await session.commit()

            res = await session.execute(
                text("SELECT state FROM promotions WHERE name = 'ФуллЦикл'")
            )
            assert res.one()._mapping["state"] == expected_state

    async def test_agent_apply_promotion_end_to_end_commits(
        self, session: AsyncSession, postgres_dsn: str,
    ) -> None:
        """PromotionAgent.apply_promotion owns commit — verify it persists
        across sessions, not just within the fixture's own transaction."""
        command = {
            "action": "create", "name": "Итоговая", "discount": 30,
            "target_promotion_name": None,
        }

        engine = create_async_engine(postgres_dsn)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        agent = PromotionAgent(session_factory=sf)

        result = await agent.apply_promotion(command)

        assert result.applied is True
        assert result.error is None

        async with sf() as verify:
            res = await verify.execute(
                text("SELECT discount, state FROM promotions WHERE name = :n"),
                {"n": "Итоговая"},
            )
            row = res.one()
            assert float(row._mapping["discount"]) == 30.0
            assert row._mapping["state"] == "CREATED"
        await engine.dispose()