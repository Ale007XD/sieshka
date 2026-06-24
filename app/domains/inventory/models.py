"""
app/domains/inventory/models.py
Inventory domain — state enum, event enum, FSM transition table.
"""
from __future__ import annotations

from enum import Enum


class InventoryState(str, Enum):
    AVAILABLE = "AVAILABLE"
    LOW_STOCK = "LOW_STOCK"
    CRITICAL = "CRITICAL"
    OUT_OF_STOCK = "OUT_OF_STOCK"


class InventoryEvent(str, Enum):
    STOCK_DECREASED = "STOCK_DECREASED"
    STOCK_LOW = "STOCK_LOW"
    STOCK_CRITICAL = "STOCK_CRITICAL"
    STOCK_DEPLETED = "STOCK_DEPLETED"
    STOCK_REPLENISHED = "STOCK_REPLENISHED"


# Graph: allowed transitions per state
INVENTORY_TRANSITIONS: dict[tuple[InventoryState, InventoryEvent], InventoryState] = {
    (InventoryState.AVAILABLE, InventoryEvent.STOCK_DECREASED): InventoryState.LOW_STOCK,
    (InventoryState.AVAILABLE, InventoryEvent.STOCK_LOW): InventoryState.LOW_STOCK,
    (InventoryState.AVAILABLE, InventoryEvent.STOCK_CRITICAL): InventoryState.CRITICAL,
    (InventoryState.LOW_STOCK, InventoryEvent.STOCK_CRITICAL): InventoryState.CRITICAL,
    (InventoryState.LOW_STOCK, InventoryEvent.STOCK_DEPLETED): InventoryState.OUT_OF_STOCK,
    (InventoryState.LOW_STOCK, InventoryEvent.STOCK_REPLENISHED): InventoryState.AVAILABLE,
    (InventoryState.CRITICAL, InventoryEvent.STOCK_DEPLETED): InventoryState.OUT_OF_STOCK,
    (InventoryState.CRITICAL, InventoryEvent.STOCK_REPLENISHED): InventoryState.AVAILABLE,
    (InventoryState.OUT_OF_STOCK, InventoryEvent.STOCK_REPLENISHED): InventoryState.AVAILABLE,
}