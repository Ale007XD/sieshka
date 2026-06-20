"""app/fsm/core/registry.py — FSM registration and lookup by domain."""
from __future__ import annotations

from typing import Any

from app.fsm.core.base import BaseFSM

_registry: dict[str, BaseFSM[Any, Any]] = {}


def register(domain: str, fsm: BaseFSM[Any, Any]) -> None:
    _registry[domain] = fsm


def get(domain: str) -> BaseFSM[Any, Any]:
    if domain not in _registry:
        raise KeyError(f"No FSM registered for domain: {domain!r}")
    return _registry[domain]


def registered_domains() -> list[str]:
    return list(_registry.keys())
