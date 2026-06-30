"""app/db_nano.py — nano-vm-mcp SQLite WAL store init (separate from PG engine)."""

from __future__ import annotations

from pathlib import Path

from nano_vm_mcp.store import ProgramStore

from app.config import settings

_store: ProgramStore | None = None


def get_store() -> ProgramStore:
    global _store
    if _store is None:
        Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
        _store = ProgramStore(settings.SQLITE_PATH)
    return _store
