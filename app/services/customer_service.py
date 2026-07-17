"""app/services/customer_service.py — Customer identity resolution.

This is the ONLY place a Customer row is created. sprint_m7_checkout_wiring
calls find_or_create_by_phone(); it never inserts a Customer directly.

normalize_phone() is a pure, side-effect-free function so it can be unit
tested in isolation and, if the OPEN QUESTION in sprint_m7_customer_domain is
resolved in favour of wrapping identity resolution as a governed TOOL step,
lifted verbatim into a Program step without change.

Normalization contract
----------------------
MUST accept exactly what the client-side checkout validation
(cart.js::validatePhone) already lets through: an 11-digit Russian number
whose first digit is 7 or 8, with arbitrary formatting characters (spaces,
dashes, parentheses, leading "+"). It is deliberately NOT stricter or looser
than that regex — same 11 digits, same 7/8 leading digit.

Canonical form: "+7" + the remaining 10 digits (an 8 is rewritten to 7,
matching the E.164 convention used by the checkout form's "+7XXXXXXXXXX"
hint). An 11-digit string starting with a digit other than 7/8 is rejected.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.domains.customer.models import Customer

logger = logging.getLogger(__name__)

# Digits-only view of an input, used to apply the client-side validatePhone
# rule (11 digits, first digit 7 or 8) regardless of formatting characters.
_PHONE_DIGITS_ONLY_RE = re.compile(r"\D")
# 11 digits starting with 7 or 8 => valid RU number per validatePhone().
_PHONE_VALID_RE = re.compile(r"^[78]\d{10}$")
# 11 digits starting with any other digit => seen at the boundary but invalid.
_PHONE_INVALID_RE = re.compile(r"^[0-69]\d{10}$")


class PhoneNormalizationError(ValueError):
    """Raised when a phone cannot be parsed into the canonical RU form."""


def normalize_phone(phone: str) -> str:
    """Normalize an RU phone to canonical "+7XXXXXXXXXX" form.

    Accepts exactly the inputs the checkout form's validatePhone() permits:
    an 11-digit RU number starting with 7 or 8, with any formatting noise
    (spaces, dashes, parentheses, a leading "+"). Raises
    PhoneNormalizationError for anything outside that rule.
    """
    if not isinstance(phone, str):
        raise PhoneNormalizationError(f"phone must be a string, got {type(phone)!r}")

    # Strip all non-digit characters, then apply validatePhone()'s rule.
    digits = _PHONE_DIGITS_ONLY_RE.sub("", phone)
    if _PHONE_VALID_RE.match(digits):
        # Rewrite a leading 8 to 7 to match the "+7XXXXXXXXXX" canonical hint.
        return "+7" + digits[1:]
    if _PHONE_INVALID_RE.match(digits):
        raise PhoneNormalizationError(
            f"phone {phone!r} is not an RU number starting with 7 or 8"
        )
    raise PhoneNormalizationError(f"phone {phone!r} is not a valid 11-digit RU number")


class CustomerService:
    """Resolves (find-or-create) a Customer by normalized phone number."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def find_or_create_by_phone(self, name: str, phone: str) -> Customer:
        canonical = normalize_phone(phone)

        async with self._session_factory() as session:
            existing = await self._find_by_phone(session, canonical)
            if existing is not None:
                return existing

            created = await self._create(session, name, canonical)
            await session.commit()
            return created

    async def _find_by_phone(
        self, session: AsyncSession, canonical_phone: str
    ) -> Customer | None:
        result = await session.execute(
            text(
                "SELECT id, name, phone, created_at "
                "FROM customers WHERE phone = :phone"
            ),
            {"phone": canonical_phone},
        )
        row = result.fetchone()
        if row is None:
            return None
        return Customer(
            id=row._mapping["id"],
            name=row._mapping["name"],
            phone=row._mapping["phone"],
            created_at=row._mapping["created_at"],
        )

    async def _create(
        self, session: AsyncSession, name: str, canonical_phone: str
    ) -> Customer:
        result = await session.execute(
            text(
                "INSERT INTO customers (name, phone) "
                "VALUES (:name, :phone) "
                "RETURNING id, name, phone, created_at"
            ),
            {"name": name, "phone": canonical_phone},
        )
        row = result.one()
        return Customer(
            id=row._mapping["id"],
            name=row._mapping["name"],
            phone=row._mapping["phone"],
            created_at=row._mapping["created_at"],
        )
