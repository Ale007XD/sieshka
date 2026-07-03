"""tests/integration/test_receipt_endpoint.py — GET /admin/orders/{id}/receipt.

Requirements:
  - ExecutionReceipt fields match nano-vm spec trace_id, trace_hash, final_status,
    resumable, replayable, blocked_actions, escalations, rejected_transitions, health
  - blocked_actions == 0, escalations == 0 (deferred until GovernanceEnvelope.decision)
  - NO custom fields added at this layer
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nano_vm.models import StepResult, StepStatus, Trace, TraceStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import router as admin_router
from app.api.routes.orders import router as orders_router
from app.db_nano import get_store
from app.services.order_service import OrderService
from app.services.trace_analyzer import ExecutionReceipt


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = (
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    )
    schema = schema_path.read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(orders_router)
    app.include_router(admin_router)

    from app.api.routes.admin import get_order_service as admin_get_service
    from app.api.routes.orders import get_order_service as orders_get_service

    async def _test_service() -> OrderService:
        return OrderService(session_factory=session_factory)

    app.dependency_overrides[orders_get_service] = _test_service
    app.dependency_overrides[admin_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestReceiptEndpoint:
    async def _create_order(self, client: AsyncClient) -> dict[str, Any]:
        payload: dict[str, object] = {
            "customer_id": str(uuid.uuid4()),
            "items": [{"sku": "coffee", "qty": 2}],
            "delivery_address": "Moscow, Red Square 1",
        }
        resp = await client.post("/orders", json=payload)
        assert resp.status_code == 201
        data: dict[str, Any] = resp.json()
        return data

    def _order_id(self, order: dict[str, Any]) -> str:
        val = order["id"]
        assert isinstance(val, str)
        return val

    async def _inject_trace_id(
        self,
        factory: async_sessionmaker[AsyncSession],
        order_id: str,
        trace_id: str,
    ) -> None:
        async with factory() as session:
            await session.execute(
                text("UPDATE orders SET trace_id = :trace_id WHERE id = :id"),
                {"trace_id": trace_id, "id": order_id},
            )
            await session.commit()

    def _build_trace(self, trace_id: str) -> Trace:
        return Trace(
            trace_id=trace_id,
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(
                    step_id="step_1",
                    status=StepStatus.SUCCESS,
                    output="ok",
                ),
            ],
        )

    def _save_trace(self, trace: Trace, trace_id: str) -> None:
        """Persist a Trace + StateContext to ProgramStore.

        StoreCursorRepository.load() requires both a trace entry and a
        state_context entry — we write both so that TraceAnalyzer.receipt()
        can find them via StoreCursorRepository.load().
        """
        store = get_store()
        dump = trace.model_dump(mode="json")
        store.save_trace(
            trace_id=trace_id,
            program_id=trace.program_name,
            status=trace.status.value,
            steps_count=len(trace.steps),
            total_cost=trace.total_cost_usd() or 0.0,
            trace=dump,
        )
        store.save_state_context(
            trace_id=trace_id,
            context={"step_id": "", "data": {}, "step_outputs": {}},
        )

    async def test_receipt_success(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        order = await self._create_order(client)
        order_id = self._order_id(order)

        trace_id = str(uuid.uuid4())
        await self._inject_trace_id(session_factory, order_id, trace_id)

        trace = self._build_trace(trace_id)
        self._save_trace(trace, trace_id)

        resp = await client.get(f"/admin/orders/{order_id}/receipt")
        assert resp.status_code == 200
        data: dict[str, Any] = resp.json()

        receipt = ExecutionReceipt(**data)
        assert receipt.trace_id == trace_id
        assert receipt.final_status == "success"
        assert receipt.resumable is False
        assert receipt.replayable is False
        assert receipt.blocked_actions == 0
        assert receipt.escalations == 0
        assert len(receipt.rejected_transitions) == 0
        assert receipt.health.total_steps == 1
        assert receipt.health.successful_steps == 1
        assert receipt.health.failed_steps == 0

    async def test_receipt_not_found(
        self,
        client: AsyncClient,
    ) -> None:
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/admin/orders/{fake_id}/receipt")
        assert resp.status_code == 404

    async def test_receipt_no_trace(
        self,
        client: AsyncClient,
    ) -> None:
        order = await self._create_order(client)
        order_id = self._order_id(order)
        resp = await client.get(f"/admin/orders/{order_id}/receipt")
        assert resp.status_code == 404

    async def test_receipt_rejected_transitions(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        order = await self._create_order(client)
        order_id = self._order_id(order)

        trace_id = str(uuid.uuid4())
        await self._inject_trace_id(session_factory, order_id, trace_id)

        trace = Trace(
            trace_id=trace_id,
            program_name="test_program",
            status=TraceStatus.FAILED,
            steps=[
                StepResult(
                    step_id="step_1",
                    status=StepStatus.SUCCESS,
                    output="ok",
                ),
                StepResult(
                    step_id="step_2",
                    status=StepStatus.FAILED,
                    error="Payment declined",
                ),
            ],
        )
        self._save_trace(trace, trace_id)

        resp = await client.get(f"/admin/orders/{order_id}/receipt")
        assert resp.status_code == 200
        data: dict[str, Any] = resp.json()

        receipt = ExecutionReceipt(**data)
        assert receipt.final_status == "failed"
        assert receipt.replayable is True
        assert receipt.blocked_actions == 0
        assert receipt.escalations == 0
        assert len(receipt.rejected_transitions) == 1
        rejected = receipt.rejected_transitions[0]
        assert rejected.step_id == "step_2"
        assert rejected.step_index == 1
        assert rejected.error == "Payment declined"
        assert receipt.health.total_steps == 2
        assert receipt.health.successful_steps == 1
        assert receipt.health.failed_steps == 1

    async def test_receipt_blocked_escalations_deferred(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Verify blocked_actions and escalations are always 0 (deferred upstream).
        
        If this test fails after a nano-vm upgrade, GovernanceEnvelope.decision
        may finally be implemented in StepResult — remove the zero-assertion
        constraint per sprint_m3_execution_receipt_wiring.md.
        """
        order = await self._create_order(client)
        order_id = self._order_id(order)

        trace_id = str(uuid.uuid4())
        await self._inject_trace_id(session_factory, order_id, trace_id)
        trace = self._build_trace(trace_id)
        self._save_trace(trace, trace_id)

        resp = await client.get(f"/admin/orders/{order_id}/receipt")
        data: dict[str, Any] = resp.json()

        assert data["blocked_actions"] == 0
        assert data["escalations"] == 0
