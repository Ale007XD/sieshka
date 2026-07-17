"""tests/unit/test_customer_service.py"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.domains.customer.models import Customer
from app.services.customer_service import (
    CustomerService,
    PhoneNormalizationError,
    normalize_phone,
)


@asynccontextmanager
async def _session_factory(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


def _row(**cols: object) -> MagicMock:
    row = MagicMock()
    row._mapping = cols
    return row


# ---------------------------------------------------------------------------
# normalize_phone — equivalence + rule-matching
# ---------------------------------------------------------------------------
class TestNormalizePhone:
    def test_equivalence_formatted_and_plain_resolve_same(self) -> None:
        """The contract: '+7 (999) 123-45-67' and '+79991234567' are ONE customer."""
        a = normalize_phone("+7 (999) 123-45-67")
        b = normalize_phone("+79991234567")
        assert a == b == "+79991234567"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+79991234567", "+79991234567"),
            ("89991234567", "+79991234567"),
            ("8 (999) 123-45-67", "+79991234567"),
            ("+8 (999) 123-45-67", "+79991234567"),
            ("7 999 123 45 67", "+79991234567"),
            ("+7(999)1234567", "+79991234567"),
            ("8-999-123-45-67", "+79991234567"),
        ],
    )
    def test_valid_variants(self, raw: str, expected: str) -> None:
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "9991234567",       # 10 digits, too short
            "99991234567",      # 11 digits but starts with 9
            "69991234567",      # 11 digits but starts with 6
            "+799912345",       # 10 digits
            "+799912345678",    # 12 digits
            "12345",            # garbage
            "",                 # empty
        ],
    )
    def test_rejects_out_of_rule(self, raw: str) -> None:
        with pytest.raises(PhoneNormalizationError):
            normalize_phone(raw)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(PhoneNormalizationError):
            normalize_phone(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CustomerService.find_or_create_by_phone
# ---------------------------------------------------------------------------
class TestCustomerService:
    async def test_creates_when_absent(self) -> None:
        customer_id = uuid4()
        find_result = MagicMock()
        find_result.fetchone.return_value = None
        create_result = MagicMock()
        create_result.one.return_value = _row(
            id=customer_id,
            name="Ivan",
            phone="+79991234567",
            created_at=None,
        )
        session = AsyncMock()
        session.execute.side_effect = [find_result, create_result]
        session.commit = AsyncMock()

        svc = CustomerService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.find_or_create_by_phone("Ivan", "8 (999) 123-45-67")

        assert isinstance(result, Customer)
        assert result.id == customer_id
        assert result.phone == "+79991234567"
        # INSERT must use the canonical form, not the raw input
        # session.execute(text(...), params) → params is the 2nd positional arg
        insert_call = session.execute.call_args_list[1]
        inserted_phone = insert_call[0][1]["phone"]
        assert inserted_phone == "+79991234567"
        session.commit.assert_called_once()

    async def test_finds_existing_does_not_insert(self) -> None:
        customer_id = uuid4()
        find_result = MagicMock()
        find_result.fetchone.return_value = _row(
            id=customer_id,
            name="Ivan",
            phone="+79991234567",
            created_at=None,
        )
        session = AsyncMock()
        session.execute.return_value = find_result
        session.commit = AsyncMock()

        svc = CustomerService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        result = await svc.find_or_create_by_phone("Ivan", "+79991234567")

        assert result.id == customer_id
        # Lookup path must not have performed an INSERT (only the SELECT ran)
        assert session.execute.call_count == 1
        session.commit.assert_not_called()

    async def test_formatted_and_plain_resolve_to_same_customer(self) -> None:
        """Equivalence end-to-end: two differently-formatted inputs → same row id."""
        customer_id = uuid4()
        find_result = MagicMock()
        find_result.fetchone.return_value = _row(
            id=customer_id,
            name="Ivan",
            phone="+79991234567",
            created_at=None,
        )
        session = AsyncMock()
        session.execute.return_value = find_result
        session.commit = AsyncMock()

        svc = CustomerService(session_factory=_session_factory)  # type: ignore[arg-type]
        svc._session_factory = lambda: _session_factory(session)  # type: ignore[assignment]

        a = await svc.find_or_create_by_phone("Ivan", "+7 (999) 123-45-67")
        b = await svc.find_or_create_by_phone("Ivan", "89991234567")

        assert a.id == b.id == customer_id
        # Both lookups used the SAME canonical phone in the WHERE clause
        looked_up_phones = [c[0][1]["phone"] for c in session.execute.call_args_list]
        assert all(p == "+79991234567" for p in looked_up_phones)
