"""tests/unit/test_idempotency.py — IdempotencyService unit tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from app.services.idempotency import IdempotencyService


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


class TestIdempotencyService:
    async def test_check_and_record_new_key(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "test-key"
        session.execute = AsyncMock(return_value=mock_result)

        svc = IdempotencyService(session_factory=lambda: _session_factory(session))  # type: ignore[arg-type]
        result = await svc.check_and_record("test-key", {"order_id": "123"})

        assert result is True

    async def test_check_and_record_duplicate_key(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        svc = IdempotencyService(session_factory=lambda: _session_factory(session))  # type: ignore[arg-type]
        result = await svc.check_and_record("dup-key", {"order_id": "456"})

        assert result is False

    async def test_check_and_record_no_payload(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "key-no-payload"
        session.execute = AsyncMock(return_value=mock_result)

        svc = IdempotencyService(session_factory=lambda: _session_factory(session))  # type: ignore[arg-type]
        result = await svc.check_and_record("key-no-payload")

        assert result is True

    async def test_check_and_record_key_format(self) -> None:
        """Verify key follows trace_id:program_step convention."""
        trace_id = "550e8400-e29b-41d4-a716-446655440000"
        program_step = "payment_confirmation"
        expected_key = f"{trace_id}:{program_step}"

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = expected_key

        captured_key: str | None = None

        async def _capture_execute(*args: object, **kwargs: object) -> MagicMock:
            nonlocal captured_key
            if len(args) >= 2 and isinstance(args[1], dict):
                captured_key = args[1].get("key", None)
            return mock_result

        session.execute = _capture_execute

        svc = IdempotencyService(session_factory=lambda: _session_factory(session))  # type: ignore[arg-type]

        await svc.check_and_record(expected_key)

        assert captured_key == expected_key
