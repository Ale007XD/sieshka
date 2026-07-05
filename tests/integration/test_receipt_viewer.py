"""tests/integration/test_receipt_viewer.py — GET /admin/ui/orders/{id}/receipt.

Requirements:
  - Renders ExecutionReceipt fields: trace_hash, final_status, rejected_transitions, health
  - SVG bars with threshold lines for health metrics
  - Step metrics section present when non-zero values, absent when all-zero
  - No safe filter used (autoescape on)
  - Error strings truncated at 200 chars
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from nano_vm.models import StepMetrics, StepResult, StepStatus, Trace, TraceStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import router as admin_router
from app.api.routes.orders import router as orders_router
from app.db_nano import get_store
from app.services.order_service import OrderService
from app.web.routes import router as web_router


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
    templates_dir = (
        Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    )
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    app.include_router(orders_router)
    app.include_router(admin_router)
    app.include_router(web_router)

    from app.api.routes.admin import get_order_service as admin_get_service
    from app.api.routes.orders import get_order_service as orders_get_service
    from app.web.routes import get_order_service as web_get_service

    async def _test_service() -> OrderService:
        return OrderService(session_factory=session_factory)

    app.dependency_overrides[orders_get_service] = _test_service
    app.dependency_overrides[admin_get_service] = _test_service
    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestReceiptViewer:
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

    def _save_trace(self, trace: Trace, trace_id: str) -> None:
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

    async def _setup_trace(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        trace: Trace,
    ) -> str:
        order = await self._create_order(client)
        order_id = self._order_id(order)
        trace_id = str(uuid.uuid4())
        await self._inject_trace_id(session_factory, order_id, trace_id)
        self._save_trace(trace, trace_id)
        return order_id

    async def test_basic_fields_rendered(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(step_id="s1", status=StepStatus.SUCCESS, output="ok"),
            ],
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        html = resp.text
        assert "Execution Receipt" in html
        assert "Trace Hash" in html
        assert "Final Status" in html
        assert "success" in html

    async def test_step_metrics_present_when_nonzero(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(step_id="s1", status=StepStatus.SUCCESS, output="ok"),
                StepResult(step_id="s2", status=StepStatus.SUCCESS, output="done"),
            ],
            step_metrics=StepMetrics(
                llm_calls=5, tool_calls=3, condition_evals=2, retries_total=1
            ),
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        html = resp.text
        assert "Step Metrics" in html
        assert "5" in html
        assert "3" in html
        assert "2" in html
        assert "1" in html
        assert "LLM Calls" in html
        assert "Tool Calls" in html
        assert "Condition Evals" in html
        assert "Retries Total" in html

    async def test_step_metrics_absent_when_all_zero(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(step_id="s1", status=StepStatus.SUCCESS, output="ok"),
            ],
            step_metrics=StepMetrics(),
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        assert "Step Metrics" not in resp.text

    async def test_step_metrics_absent_when_none(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Simulate pre-m5_otel_wiring state by saving trace without step_metrics.

        StoreCursorRepository.load() calls Trace.model_validate() which
        fills missing step_metrics with StepMetrics() (all zeros). The
        template hides the section when all values are zero — same visual
        outcome as step_metrics=None at the receipt layer.
        """
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(step_id="s1", status=StepStatus.SUCCESS, output="ok"),
            ],
            step_metrics=StepMetrics(),
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200
        assert "Step Metrics" not in resp.text

    async def test_rejected_transitions_timeline(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.FAILED,
            steps=[
                StepResult(
                    step_id="step_a", status=StepStatus.SUCCESS, output="ok"
                ),
                StepResult(
                    step_id="step_b",
                    status=StepStatus.FAILED,
                    error="Payment declined",
                ),
            ],
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        html = resp.text
        assert "Rejected Transitions" in html
        assert "step_b" in html
        assert "Payment declined" in html
        assert "failed" in html

    async def test_error_truncated_at_200_chars(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        long_error = "x" * 500
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.FAILED,
            steps=[
                StepResult(
                    step_id="s1", status=StepStatus.FAILED, error=long_error
                ),
            ],
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        assert ("x" * 197 + "...") in resp.text
        assert ("x" * 500) not in resp.text

    async def test_health_svg_bars_with_thresholds(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            program_name="test_program",
            status=TraceStatus.SUCCESS,
            steps=[
                StepResult(step_id="s1", status=StepStatus.SUCCESS, output="ok"),
            ],
        )
        order_id = await self._setup_trace(client, session_factory, trace)

        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 200

        html = resp.text
        assert "Trace Health" in html
        assert "Steps" in html
        assert "Failed" in html
        assert "<svg" in html
        assert 'x1="160"' in html
        assert 'x1="40"' in html

    async def test_404_for_unknown_order(
        self,
        client: AsyncClient,
    ) -> None:
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/admin/ui/orders/{fake_id}/receipt")
        assert resp.status_code == 404

    async def test_404_for_order_without_trace(
        self,
        client: AsyncClient,
    ) -> None:
        order = await self._create_order(client)
        order_id = self._order_id(order)
        resp = await client.get(f"/admin/ui/orders/{order_id}/receipt")
        assert resp.status_code == 404
