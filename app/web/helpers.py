"""app/web/helpers.py — typed dict-lookups for state->color mapping.

Templates call these helpers; they do not embed the mapping logic.
"""
from __future__ import annotations

from app.domains.inventory.models import InventoryState
from app.domains.promotions.models import PromotionState

INVENTORY_STATE_COLOR: dict[InventoryState, str] = {
    InventoryState.AVAILABLE: "",
    InventoryState.LOW_STOCK: "bg-amber-50",
    InventoryState.CRITICAL: "bg-red-50",
    InventoryState.OUT_OF_STOCK: "line-through text-gray-400",
}

PROMOTION_STATE_COLOR: dict[PromotionState, str] = {
    PromotionState.CREATED: "text-gray-500",
    PromotionState.ACTIVE: "text-green-600 font-semibold",
    PromotionState.EXPIRED: "text-gray-400",
    PromotionState.ARCHIVED: "text-gray-300",
}
