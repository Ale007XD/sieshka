"""
app/domains/privacy/models.py
Privacy domain — customer data state enum, event enum, and FSM transitions.
"""
from __future__ import annotations

from enum import Enum


class CustomerDataState(str, Enum):
    ACTIVE = "ACTIVE"
    RETAINED = "RETAINED"
    ANONYMIZED = "ANONYMIZED"
    DELETED = "DELETED"


class CustomerDataEvent(str, Enum):
    RETAIN = "RETAIN"
    ANONYMIZE = "ANONYMIZE"
    GDPR_ERASE = "GDPR_ERASE"


# Graph: allowed transitions per state
CUSTOMER_DATA_TRANSITIONS: dict[tuple[CustomerDataState, CustomerDataEvent], CustomerDataState] = {
    (CustomerDataState.ACTIVE, CustomerDataEvent.RETAIN): CustomerDataState.RETAINED,
    (CustomerDataState.RETAINED, CustomerDataEvent.ANONYMIZE): CustomerDataState.ANONYMIZED,
    (CustomerDataState.ANONYMIZED, CustomerDataEvent.GDPR_ERASE): CustomerDataState.DELETED,
}