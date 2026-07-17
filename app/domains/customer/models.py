"""app/domains/customer/models.py — Customer domain Pydantic models.

Mirrors the `customers` table created by migrations/005_customer.sql.
Customer is the only persisted identity for an end user; it is created
exclusively through app.services.customer_service.find_or_create_by_phone().
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Customer(BaseModel):
    id: UUID
    name: str
    phone: str  # canonical E.164-ish form, e.g. "+79991234567"
    created_at: datetime | None = None
