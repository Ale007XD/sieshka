"""tests/unit/test_governance.py — PolicySnapshot + GovernedToolExecutor."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from nano_vm import GovernanceEnvelope

from app.domains.orders.models import OrderEvent, OrderState
from app.fsm.core.base import TransitionResult
from app.policy.policy_snapshot import (
    DELIVERY_POLICY_SNAPSHOT,
    KITCHEN_POLICY_SNAPSHOT,
    KITCHEN_TOOL_CAPABILITIES,
    ORDERS_POLICY_SNAPSHOT,
    ORDERS_TOOL_CAPABILITIES,
)
from app.repositories.order_repo import OrderRepository
from app.services.order_service import OrderService, _governed_tool


class TestPolicySnapshots:
    def test_orders_snapshot_has_expected_tools(self) -> None:
        allowed = ORDERS_POLICY_SNAPSHOT.allowed_tools()
        assert allowed == set(ORDERS_TOOL_CAPABILITIES.keys())

    def test_orders_snapshot_allows_tool(self) -> None:
        assert ORDERS_POLICY_SNAPSHOT.allows_tool("validate_order_items") is True
        assert ORDERS_POLICY_SNAPSHOT.allows_tool("unknown_tool") is False

    def test_orders_snapshot_has_capability(self) -> None:
        assert ORDERS_POLICY_SNAPSHOT.has_capability("validate_order_items", "orders:read")
        assert ORDERS_POLICY_SNAPSHOT.has_capability("unknown_tool", "orders:read") is False

    def test_orders_snapshot_required_capabilities(self) -> None:
        caps = ORDERS_POLICY_SNAPSHOT.required_capabilities("transition_order_state")
        assert caps == ["orders:write"]

    def test_orders_snapshot_policy_id_and_version(self) -> None:
        assert ORDERS_POLICY_SNAPSHOT.policy_id == "orders-v1"
        assert ORDERS_POLICY_SNAPSHOT.version == "1.0.0"

    def test_orders_snapshot_policy_hash(self) -> None:
        assert len(ORDERS_POLICY_SNAPSHOT.policy_hash) == 64

    def test_kitchen_snapshot_has_expected_tools(self) -> None:
        allowed = KITCHEN_POLICY_SNAPSHOT.allowed_tools()
        assert allowed == set(KITCHEN_TOOL_CAPABILITIES.keys())

    def test_delivery_snapshot_empty_tools(self) -> None:
        assert DELIVERY_POLICY_SNAPSHOT.allowed_tools() == set()

    def test_snapshots_have_different_policy_hashes(self) -> None:
        assert ORDERS_POLICY_SNAPSHOT.policy_hash != KITCHEN_POLICY_SNAPSHOT.policy_hash


class TestGovernanceEnvelopeVerification:
    def test_verify_policy_returns_true_for_matching_snapshot(self) -> None:
        envelope = GovernanceEnvelope(
            execution_id=str(uuid4()),
            step_id=0,
            policy_hash=ORDERS_POLICY_SNAPSHOT.policy_hash,
            canonical_snapshot_hash="abc123",
            payload={"status": "ok"},
        )
        assert envelope.verify_policy(ORDERS_POLICY_SNAPSHOT) is True

    def test_verify_policy_returns_false_for_wrong_snapshot(self) -> None:
        envelope = GovernanceEnvelope(
            execution_id=str(uuid4()),
            step_id=0,
            policy_hash="wrong_hash",
            canonical_snapshot_hash="abc123",
            payload={"status": "ok"},
        )
        assert envelope.verify_policy(ORDERS_POLICY_SNAPSHOT) is False


class TestGovernedToolExecutorChecks:
    def test_check_allowed_tool_passes(self) -> None:
        from nano_vm_mcp.handlers import GovernedToolExecutor

        executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
        executor.check("validate_order_items")

    def test_check_denied_tool_raises(self) -> None:
        from nano_vm_mcp.handlers import CapabilityDeniedError, GovernedToolExecutor

        executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
        with pytest.raises(CapabilityDeniedError, match="unknown_tool"):
            executor.check("unknown_tool")

    def test_check_none_policy_allows_all(self) -> None:
        from nano_vm_mcp.handlers import GovernedToolExecutor

        executor = GovernedToolExecutor(policy=None)
        executor.check("any_tool")

    def test_is_allowed_returns_bool(self) -> None:
        from nano_vm_mcp.handlers import GovernedToolExecutor

        executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
        assert executor.is_allowed("validate_order_items") is True
        assert executor.is_allowed("unknown_tool") is False


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestGovernedToolExecutorWiredInService:
    async def test_transition_order_success_with_governance(self) -> None:
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

        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state == OrderState.CONFIRMED
        session.commit.assert_called_once()

    async def test_governed_tool_wrapper_delegates_allowed(self) -> None:
        from nano_vm_mcp.handlers import GovernedToolExecutor

        fn = AsyncMock(return_value="done")
        executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
        wrapped = _governed_tool(fn, "validate_order_items", executor)
        result = await wrapped()
        assert result == "done"
        fn.assert_awaited_once()

    async def test_governed_tool_wrapper_blocks_denied(self) -> None:
        from nano_vm_mcp.handlers import CapabilityDeniedError, GovernedToolExecutor

        fn = AsyncMock()
        executor = GovernedToolExecutor(policy=ORDERS_POLICY_SNAPSHOT)
        wrapped = _governed_tool(fn, "unknown_tool", executor)
        with pytest.raises(CapabilityDeniedError, match="unknown_tool"):
            await wrapped()
        fn.assert_not_awaited()
