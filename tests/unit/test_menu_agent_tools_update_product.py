"""tests/unit/test_menu_agent_tools_update_product.py — sort field on
update_product (sprint_menu_product_reorder, 2026-08-17).

Mocked session, no Postgres — covers only the parsing/COALESCE-wiring layer
that's testable without a live DB. The TOCTOU write path itself is exercised
by tests/integration/test_menu_agent_apply_phase.py-style tests (not added
here — this repo's gate sequence runs `pytest tests/unit -m "not
integration"` only, so a Postgres-backed test wouldn't add CI coverage;
see CONSTRAINTS.md's Sieshka integration-tests notes for why those live in
a separate suite gated on a running sieshka-postgres-1 container).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.menu_agent_tools import (
    _required_update_product_fields,
    apply_update_product_command,
)


class TestRequiredUpdateProductFieldsSort:
    def test_sort_absent_is_none(self) -> None:
        parsed = _required_update_product_fields(
            {"product_id": "11111111-1111-1111-1111-111111111111"}
        )
        assert parsed is not None
        assert parsed[8] is None  # sort

    def test_sort_valid_int(self) -> None:
        parsed = _required_update_product_fields(
            {"product_id": "11111111-1111-1111-1111-111111111111", "sort": 20}
        )
        assert parsed is not None
        assert parsed[8] == 20

    def test_sort_zero_is_valid(self) -> None:
        """0 is a legitimate sort value (default), must not be treated as falsy-absent."""
        parsed = _required_update_product_fields(
            {"product_id": "11111111-1111-1111-1111-111111111111", "sort": 0}
        )
        assert parsed is not None
        assert parsed[8] == 0

    def test_sort_bool_rejected(self) -> None:
        """bool is a subclass of int in Python — must be explicitly excluded
        (same guard as _required_update_category_fields's sort field)."""
        parsed = _required_update_product_fields(
            {"product_id": "11111111-1111-1111-1111-111111111111", "sort": True}
        )
        assert parsed is None

    def test_sort_non_int_rejected(self) -> None:
        parsed = _required_update_product_fields(
            {"product_id": "11111111-1111-1111-1111-111111111111", "sort": "20"}
        )
        assert parsed is None


class TestApplyUpdateProductCommandSort:
    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        session = AsyncMock()
        existing_row = MagicMock()
        existing_row.fetchall.return_value = [{"id": "x"}]  # product found under FOR UPDATE
        session.execute.return_value = existing_row
        return session

    async def test_sort_passed_through_to_write_params(
        self, mock_session: AsyncMock,
    ) -> None:
        """sort must reach the UPDATE statement's bound params — COALESCE at
        the SQL layer handles 'leave unchanged' when absent; this only
        verifies the Python-side plumbing carries the value through."""
        command = {
            "product_id": "11111111-1111-1111-1111-111111111111",
            "sort": 30,
        }
        await apply_update_product_command(session=mock_session, command=command)

        # Last execute() call is the UPDATE — inspect its bound params.
        _args, kwargs_or_params = mock_session.execute.call_args
        bound = _args[1] if len(_args) > 1 else None
        assert bound is not None
        assert bound["sort"] == 30

    async def test_sort_absent_passes_none(self, mock_session: AsyncMock) -> None:
        command = {"product_id": "11111111-1111-1111-1111-111111111111"}
        await apply_update_product_command(session=mock_session, command=command)

        _args, _kwargs = mock_session.execute.call_args
        bound = _args[1] if len(_args) > 1 else None
        assert bound is not None
        assert bound["sort"] is None
