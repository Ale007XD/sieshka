"""tests/unit/test_order_tools_session_di.py — session injected by caller, not self-opened.

Verifies CONSTRAINT: tool получает session от вызывающего, не открывает свою;
commit НЕ вызывается внутри tool (caller owns transaction boundary).
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import app.tools.order_tools as ot
from app.tools.order_tools import (
    create_kitchen_ticket,
    reserve_inventory_items,
    transition_order_state,
    validate_order_items,
    write_order_state_cooking,
    write_order_state_paid,
    write_order_state_payment_failed,
    write_order_state_payment_pending,
)


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


_SESSION_TOOL_NAMES: list[str] = [
    "validate_order_items",
    "write_order_state_payment_pending",
    "write_order_state_paid",
    "write_order_state_payment_failed",
    "write_order_state_cooking",
    "reserve_inventory_items",
    "create_kitchen_ticket",
    "transition_order_state",
]

_ID: str = str(uuid4())
_SESSION_TOOLS: list[tuple[str, Callable[..., Any], list[object], dict[str, object]]] = [
    ("validate_order_items", validate_order_items, [_ID], {}),
    ("write_order_state_payment_pending", write_order_state_payment_pending, [_ID, "pi_123"], {}),
    ("write_order_state_paid", write_order_state_paid, [_ID], {}),
    ("write_order_state_payment_failed", write_order_state_payment_failed, [_ID], {}),
    ("write_order_state_cooking", write_order_state_cooking, [_ID, _ID], {}),
    ("reserve_inventory_items", reserve_inventory_items, [_ID], {}),
    ("create_kitchen_ticket", create_kitchen_ticket, [_ID], {}),
    ("transition_order_state", transition_order_state, [_ID, "DRAFT", "CONFIRMED"], {}),
]

# Tools that raise ValueError when order row is None
_RAISE_ON_NONE: frozenset[str] = frozenset({
    "write_order_state_payment_pending",
    "write_order_state_paid",
    "write_order_state_payment_failed",
    "write_order_state_cooking",
    "transition_order_state",
})


class TestSessionInjection:
    """Every DB-writing tool receives session as first positional arg (not **kwargs)."""

    @pytest.mark.parametrize(
        "name,tool,args,kwargs", _SESSION_TOOLS, ids=[t[0] for t in _SESSION_TOOLS],
    )
    async def test_accepts_session_as_first_arg(
        self, mock_session: AsyncMock, name: str,
        tool: Callable[..., Any], args: list[object],
        kwargs: dict[str, object],
    ) -> None:
        mock_session.execute.return_value = MagicMock()
        mock_session.execute.return_value.one.return_value = MagicMock()
        mock_session.execute.return_value.one.return_value._mapping = {"id": _ID}

        if name in _RAISE_ON_NONE:
            mock_session.execute.return_value.scalar_one_or_none.return_value = None
            with pytest.raises(ValueError):
                await tool(mock_session, *args, **kwargs)
        else:
            scalar = mock_session.execute.return_value.scalar_one_or_none
            if name == "reserve_inventory_items":
                scalar.side_effect = [
                    '[{"sku": "coffee", "qty": 2}]',   # items query
                    10,                                  # stock query
                ]
            else:
                scalar.return_value = '[{"sku": "coffee", "qty": 2}]'
            await tool(mock_session, *args, **kwargs)

    @pytest.mark.parametrize(
        "name,tool,args,kwargs", _SESSION_TOOLS, ids=[t[0] for t in _SESSION_TOOLS],
    )
    async def test_does_not_commit(
        self, mock_session: AsyncMock, name: str,
        tool: Callable[..., Any], args: list[object],
        kwargs: dict[str, object],
    ) -> None:
        mock_session.execute.return_value = MagicMock()
        mock_session.execute.return_value.one.return_value = MagicMock()
        mock_session.execute.return_value.one.return_value._mapping = {"id": _ID}

        if name in _RAISE_ON_NONE:
            mock_session.execute.return_value.scalar_one_or_none.return_value = None
            with pytest.raises(ValueError):
                await tool(mock_session, *args, **kwargs)
        else:
            scalar = mock_session.execute.return_value.scalar_one_or_none
            if name == "reserve_inventory_items":
                scalar.side_effect = [
                    '[{"sku": "coffee", "qty": 2}]',   # items query
                    10,                                  # stock query
                ]
            else:
                scalar.return_value = '[{"sku": "coffee", "qty": 2}]'
            await tool(mock_session, *args, **kwargs)

        mock_session.commit.assert_not_called()


class TestSessionNotInKwargs:
    """Session is NOT passed through **kwargs — only as explicit first param."""

    async def test_validate_order_items_no_session_in_kwargs(self) -> None:
        import inspect

        sig = inspect.signature(validate_order_items)
        params = list(sig.parameters.keys())
        assert params[0] == "session", f"Expected 'session' as first param, got {params}"

    async def test_session_not_swallowed_by_kwargs(self) -> None:
        """session is an explicit first param, never swallowed by **kwargs."""
        for name in _SESSION_TOOL_NAMES:
            fn = getattr(ot, name)
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            assert params[0] == "session", (
                f"{name}: expected 'session' as first param, got {params}"
            )
            assert "session" not in params[1:], (
                f"{name}: 'session' leaked into positional args beyond position 0"
            )
