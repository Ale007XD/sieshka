"""tests/unit/test_db_nano.py — store init smoke test (connects, WAL, no PG coupling)."""

from __future__ import annotations

import sqlite3
import tempfile

from nano_vm_mcp.store import ProgramStore

from app.db_nano import get_store


class TestDbNanoStoreInit:
    def test_store_constructs_and_connects(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = ProgramStore(f.name)
            assert store is not None
            store.close()

    def test_wal_mode_is_set(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ProgramStore(f.name)
            con = sqlite3.connect(f.name)
            row = con.execute("PRAGMA journal_mode").fetchone()
            assert row is not None
            assert row[0].upper() == "WAL"
            con.close()

    def test_schema_tables_exist(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ProgramStore(f.name)
            con = sqlite3.connect(f.name)
            tables = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            names = {r[0] for r in tables}
            for expected in (
                "programs",
                "traces",
                "state_contexts",
                "governance_envelopes",
                "idempotency_keys",
                "execution_traces",
                "transition_stats",
            ):
                assert expected in names, f"missing table: {expected}"
            con.close()

    def test_get_store_returns_singleton(self) -> None:
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2


class TestDbNanoNoPGCoupling:
    def test_db_nano_imports_no_pg_modules(self) -> None:
        import sys

        pg_modules = {"asyncpg", "sqlalchemy", "app.db"}
        std = set(sys.modules)
        import app.db_nano  # noqa: F401  trigger module import to check sys.modules

        std_after = set(sys.modules)
        imported = std_after - std
        assert not (pg_modules & imported), (
            f"app.db_nano triggered PG imports: {pg_modules & imported}"
        )
