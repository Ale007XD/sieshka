"""tests/integration/test_execution_vm_orders.py — full order lifecycle through ExecutionVM.

Requires Docker (testcontainers). Skipped if not available.
SQLite nano-vm store is created in temp directory.
"""
from __future__ import annotations

import tempfile
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import asyncpg
import pytest
from nano_vm.vm import ExecutionVM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db_nano import StoreCursorRepository
from app.domains.orders.models import OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.repositories.order_repo import OrderRepository

# Re-use conftest's docker detection + postgres fixture
pytestmark = [
    pytest.mark.integration,
]


@pytest.fixture(scope="module")
def nano_store_path() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture(scope="module")
def _init_nano_store(nano_store_path: str) -> None:
    # Override SQLITE_PATH before first call to get_store()
    import app.db_nano as dbn
    from app.config import settings
    from app.db_nano import get_store as get_nano_store

    dbn._store = None
    original_path = settings.SQLITE_PATH
    try:
        settings.SQLITE_PATH = nano_store_path
        store = get_nano_store()
        assert store is not None
    finally:
        settings.SQLITE_PATH = original_path


@pytest.fixture
async def repo(postgres_dsn: str) -> AsyncGenerator[OrderRepository, None]:
    """Creates schema, yields OrderRepository, drops tables after test."""
    engine = create_async_engine(postgres_dsn)
    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    schema = schema_path.read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
    finally:
        await conn.close()

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield OrderRepository(session)

    await engine.dispose()


@pytest.fixture
async def nano_vm(repo: OrderRepository) -> AsyncGenerator[ExecutionVM, None]:
    """Builds a fresh ExecutionVM for each test with temp nano store.

    Tool registration mirrors app.services.order_service._SESSION_TOOLS:
    since sprint_m3_session_boundary_fix (2026-07-01) removed each tool's own
    independent session_factory(), DB-writing tools require session
    closure-injected at registration time — same pattern as _build_vm().
    """
    import functools

    from nano_vm.adapters import MockLLMAdapter
    from nano_vm.vm import ExecutionVM

    from app.services.order_service import _SESSION_TOOLS
    from app.tools.order_tools import (
        create_kitchen_ticket,
        log_validation_failure,
        notify_inventory_insufficient,
        reserve_inventory_items,
        transition_order_state,
        validate_order_items,
        write_order_state_cooking,
        write_order_state_paid,
        write_order_state_payment_failed,
        write_order_state_payment_pending,
        yookassa_create_payment,
        yookassa_verify_payment,
    )

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        from nano_vm_mcp.store import ProgramStore

        store = ProgramStore(tmp.name)
        cursor = StoreCursorRepository(store)
        vm = ExecutionVM(
            llm=MockLLMAdapter(""),
            cursor_repository=cursor,
        )
        for fn in (
            validate_order_items,
            yookassa_create_payment,
            yookassa_verify_payment,
            write_order_state_payment_pending,
            write_order_state_paid,
            write_order_state_payment_failed,
            write_order_state_cooking,
            reserve_inventory_items,
            create_kitchen_ticket,
            log_validation_failure,
            notify_inventory_insufficient,
            transition_order_state,
        ):
            if fn.__name__ in _SESSION_TOOLS:
                vm.register_tool(fn.__name__, functools.partial(fn, session=repo._session))
            else:
                vm.register_tool(fn.__name__, fn)
        yield vm
        store.close()


async def _insert_order(repo: OrderRepository, state: OrderState) -> str:
    """Insert an order row and return its id as string."""
    order_id = uuid.uuid4()
    await repo._session.execute(
        text(
            "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
            "VALUES (:id, :cid, :state, :items, :addr)"
        ),
        {
            "id": order_id,
            "cid": uuid.uuid4(),
            "state": state.value,
            "items": '[{"sku": "coffee", "qty": 2}]',
            "addr": "Moscow",
        },
    )
    await repo._session.commit()
    return str(order_id)


class TestOrderLifecycleViaExecutionVM:
    """Full order lifecycle through ExecutionVM, NOT OrderFSM."""

    async def test_draft_to_cancelled(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        order_id = await _insert_order(repo, OrderState.DRAFT)

        program = Program(
            name="order_cancel",
            steps=[
                Step(
                    id="write_cancelled",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "DRAFT",
                        "to_state": "CANCELLED",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": order_id})
        assert trace.status == TraceStatus.SUCCESS
        assert await repo.get_state(order_id) == OrderState.CANCELLED

    async def test_draft_to_confirmed(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        order_id = await _insert_order(repo, OrderState.DRAFT)

        program = Program(
            name="order_confirm",
            steps=[
                Step(
                    id="write_confirmed",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "DRAFT",
                        "to_state": "CONFIRMED",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": order_id})
        assert trace.status == TraceStatus.SUCCESS
        assert await repo.get_state(order_id) == OrderState.CONFIRMED

    async def test_confirmed_to_payment_pending_simple(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        order_id = await _insert_order(repo, OrderState.CONFIRMED)

        program = Program(
            name="order_request_payment_simple",
            steps=[
                Step(
                    id="write_payment_pending",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "CONFIRMED",
                        "to_state": "PAYMENT_PENDING",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": order_id})
        assert trace.status == TraceStatus.SUCCESS
        assert await repo.get_state(order_id) == OrderState.PAYMENT_PENDING

    async def test_payment_pending_to_paid_simple(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        order_id = await _insert_order(repo, OrderState.PAYMENT_PENDING)

        program = Program(
            name="order_payment_confirmed_simple",
            steps=[
                Step(
                    id="write_paid",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "PAYMENT_PENDING",
                        "to_state": "PAID",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": order_id})
        assert trace.status == TraceStatus.SUCCESS
        assert await repo.get_state(order_id) == OrderState.PAID

    async def test_paid_to_cooking_via_full_program(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        """Uses the full PROGRAM_START_COOKING (inventory + ticket + state write)."""
        from nano_vm.models import Trace, TraceStatus

        from app.programs.order_programs import PROGRAM_START_COOKING

        order_id = await _insert_order(repo, OrderState.PAID)
        # inventory table is never seeded by migrations/other fixtures — without this,
        # reserve_inventory_items correctly finds no "coffee" row (stock_row is None) and
        # takes the insufficient-stock branch (notify_inventory_insufficient), which is
        # then structurally SUCCESS (not a bug) but never reaches write_cooking_state.
        await repo._session.execute(
            text(
                "INSERT INTO inventory (sku, name, quantity) VALUES (:sku, :name, :qty) "
                "ON CONFLICT (sku) DO UPDATE SET quantity = EXCLUDED.quantity"
            ),
            {"sku": "coffee", "name": "Coffee", "qty": 10},
        )
        await repo._session.commit()

        trace: Trace = await nano_vm.run(
            PROGRAM_START_COOKING,
            context={"order_id": order_id},
        )
        assert trace.status == TraceStatus.SUCCESS
        assert await repo.get_state(order_id) == OrderState.COOKING

    async def test_invalid_event_rejected(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        """transition_order_state's only guarantee is current==from_state (race guard via
        FOR UPDATE re-check) — it has no access to ORDER_TRANSITIONS and does not (and should
        not) validate graph edges; that lives exclusively in OrderService.transition_order
        (see test_service_rejects_invalid_event). This test exercises the actual contract:
        a stale/wrong from_state precondition is rejected."""
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        order_id = await _insert_order(repo, OrderState.DRAFT)

        program = Program(
            name="order_invalid",
            steps=[
                Step(
                    id="write_invalid",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "CONFIRMED",  # actual state is DRAFT — stale precondition
                        "to_state": "PAID",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": order_id})
        assert trace.status == TraceStatus.FAILED
        # write must not have happened — state stays DRAFT
        assert await repo.get_state(order_id) == OrderState.DRAFT

    async def test_unknown_order_rejected(
        self, repo: OrderRepository, nano_vm: ExecutionVM,
    ) -> None:
        from nano_vm.models import Program, Step, StepType, Trace, TraceStatus

        fake_id = str(uuid.uuid4())

        program = Program(
            name="order_confirm",
            steps=[
                Step(
                    id="write_confirmed",
                    type=StepType.TOOL,
                    tool="transition_order_state",
                    args={
                        "order_id": "$order_id",
                        "from_state": "DRAFT",
                        "to_state": "CONFIRMED",
                    },
                    output_key="write_result",
                    is_terminal=True,
                ),
            ],
        )
        trace: Trace = await nano_vm.run(program, context={"order_id": fake_id})
        assert trace.status == TraceStatus.FAILED

    async def test_full_lifecycle_via_order_service(
        self, repo: OrderRepository, postgres_dsn: str,
    ) -> None:
        """End-to-end: OrderService.transition_order() with ExecutionVM dispatch."""
        from app.services.order_service import OrderService

        order_id = await _insert_order(repo, OrderState.DRAFT)

        engine = create_async_engine(postgres_dsn)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        svc = OrderService(session_factory=async_session)

        # CONFIRM: DRAFT → CONFIRMED
        result = await svc.transition_order(order_id, OrderEvent.CONFIRM)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.CONFIRMED

        # REQUEST_PAYMENT: CONFIRMED → PAYMENT_PENDING (simple state write only)
        result = await svc.transition_order(order_id, OrderEvent.REQUEST_PAYMENT)
        assert result.success is True
        assert result.new_state == OrderState.PAYMENT_PENDING

        # PAYMENT_CONFIRMED: PAYMENT_PENDING → PAID (simple state write only)
        result = await svc.transition_order(order_id, OrderEvent.PAYMENT_CONFIRMED)
        assert result.success is True
        assert result.new_state == OrderState.PAID

        # START_COOKING: PAID → COOKING (full program with inventory + ticket)
        result = await svc.transition_order(order_id, OrderEvent.START_COOKING)
        assert result.success is True
        assert result.new_state == OrderState.COOKING

        await engine.dispose()

    async def test_service_rejects_invalid_event(
        self, repo: OrderRepository, postgres_dsn: str,
    ) -> None:
        from app.services.order_service import OrderService

        order_id = await _insert_order(repo, OrderState.DRAFT)

        engine = create_async_engine(postgres_dsn)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        svc = OrderService(session_factory=async_session)

        # DRAFT → PAID is not valid
        result = await svc.transition_order(order_id, OrderEvent.PAYMENT_CONFIRMED)
        assert result.success is False
        assert result.rejected_event == OrderEvent.PAYMENT_CONFIRMED

        await engine.dispose()
