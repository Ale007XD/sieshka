"""
app/domains/promotions/models.py
Promotions domain — state enum, event enum, and FSM transition table.
"""
from __future__ import annotations

from enum import Enum


class PromotionState(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class PromotionEvent(str, Enum):
    ACTIVATE = "ACTIVATE"
    EXPIRE = "EXPIRE"
    ARCHIVE = "ARCHIVE"


# Graph: allowed transitions per state
PROMOTION_TRANSITIONS: dict[tuple[PromotionState, PromotionEvent], PromotionState] = {
    (PromotionState.CREATED, PromotionEvent.ACTIVATE): PromotionState.ACTIVE,
    (PromotionState.ACTIVE, PromotionEvent.EXPIRE): PromotionState.EXPIRED,
    (PromotionState.EXPIRED, PromotionEvent.ARCHIVE): PromotionState.ARCHIVED,
}