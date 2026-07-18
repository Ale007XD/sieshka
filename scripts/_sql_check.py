import sys
from pathlib import Path

sys.path.insert(0, "migrations")
from _split_sql import run_sql_file  # noqa: E402

calls: list[str] = []


def fake_execute(sql, *a, **k):
    calls.append(sql)


m = type("M", (), {"execute": staticmethod(fake_execute)})()
run_sql_file(m, Path("migrations/009_zone_name_unique_index.sql"))
print("SQL parsed into", len(calls), "statement(s):")
for c in calls:
    print("  -", " ".join(c.split())[:90])
