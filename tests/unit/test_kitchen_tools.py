"""tests/unit/test_kitchen_tools.py — session is closure-injected, not opened by tool.
Session provided directly by test (mocked), no async_session_factory patching needed.
Mirrors tests/unit/test_order_tools.py pattern (CONSTRAINTS.md 2026-07-02:
terminal TOOL step failure propagation — raise, not ERROR sentinel)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.tools.kitchen_tools import (
    write_kitchen_state_handed_off,
    write_kitchen_state_preparing,
    write_kitchen_state_queued,
    write_kitchen_state_ready,
)


@pytest.fixture
def mock_session():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestWriteKitchenStateQueued:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "NEW"

        result = await write_kitchen_state_queued(mock_session, ticket_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PREPARING"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_kitchen_state_queued(mock_session, ticket_id=str(uuid4()))

    async def test_ticket_not_found(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="ticket not found"):
            await write_kitchen_state_queued(mock_session, ticket_id=str(uuid4()))


class TestWriteKitchenStatePreparing:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "QUEUED"

        result = await write_kitchen_state_preparing(mock_session, ticket_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "NEW"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_kitchen_state_preparing(mock_session, ticket_id=str(uuid4()))

    async def test_ticket_not_found(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="ticket not found"):
            await write_kitchen_state_preparing(mock_session, ticket_id=str(uuid4()))


class TestWriteKitchenStateReady:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PREPARING"

        result = await write_kitchen_state_ready(mock_session, ticket_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "QUEUED"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_kitchen_state_ready(mock_session, ticket_id=str(uuid4()))

    async def test_ticket_not_found(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="ticket not found"):
            await write_kitchen_state_ready(mock_session, ticket_id=str(uuid4()))


class TestWriteKitchenStateHandedOff:
    async def test_success(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "READY"

        result = await write_kitchen_state_handed_off(mock_session, ticket_id=str(uuid4()))

        assert result == "OK"
        mock_session.commit.assert_not_called()

    async def test_wrong_state(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = "PREPARING"

        with pytest.raises(ValueError, match="invalid state transition"):
            await write_kitchen_state_handed_off(mock_session, ticket_id=str(uuid4()))

    async def test_ticket_not_found(self, mock_session):
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ValueError, match="ticket not found"):
            await write_kitchen_state_handed_off(mock_session, ticket_id=str(uuid4()))
