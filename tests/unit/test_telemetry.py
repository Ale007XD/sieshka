from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.domains.orders.models import OrderEvent, OrderState
from app.repositories.order_repo import OrderRepository
from app.services.order_service import OrderService


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


@pytest.fixture(scope="session")
def span_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture(autouse=True)
def _clear_exporter(span_exporter: InMemorySpanExporter) -> None:
    span_exporter.clear()


class TestOrderTelemetry:
    async def test_transition_creates_parent_and_child_spans(
        self,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = "DRAFT"
        session.execute.return_value = mock_select
        session.commit = AsyncMock()

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert result.success is True

        spans = span_exporter.get_finished_spans()
        assert len(spans) > 0

        parent_spans = [s for s in spans if s.name == "sieshka.order_transition"]
        assert len(parent_spans) == 1

        parent = parent_spans[0]
        attrs = parent.attributes or {}
        assert attrs.get("order_id") == order_id
        assert attrs.get("event_type") == "CONFIRM"
        assert attrs.get("program_name") == "order_confirm"

        child_spans = [s for s in spans if s.name.startswith("nano_vm.step.")]
        assert len(child_spans) == 1

        for child in child_spans:
            assert child.parent is not None
            assert child.parent.span_id == parent.context.span_id

    async def test_span_count_matches_steps(
        self,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        order_id = str(uuid4())
        session = AsyncMock()
        mock_select = MagicMock()
        mock_select.scalar_one_or_none.return_value = "DRAFT"
        session.execute.return_value = mock_select
        session.commit = AsyncMock()

        svc = OrderService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        with patch.object(OrderRepository, "get_state", return_value=OrderState.DRAFT):
            result = await svc.transition_order(order_id, OrderEvent.CONFIRM)

        assert result.success is True

        spans = span_exporter.get_finished_spans()
        child_spans = [s for s in spans if s.name.startswith("nano_vm.step.")]
        assert len(child_spans) == 1
