"""PostgreSQL script splitter for Alembic async migrations.

asyncpg (used by Alembic's async env) rejects a multi-statement string in a
single execute(), unlike psql's simple query protocol used by
docker-entrypoint-initdb.d. Splitting the .sql migration files into individual
statements lets ``op.execute`` run them under asyncpg.

The splitter respects single/double quoted strings, dollar-quoting
(``$tag$`` and bare ``$$``, e.g. PL/pgSQL function bodies), and ``--`` / ``/* */``
comments, so ``DO $$ ... $$;`` and ``CREATE FUNCTION ... $$ ... $$ LANGUAGE plpgsql;``
blocks stay intact.
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import op
from sqlalchemy import text

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    in_single = in_double = in_line = in_block = False
    dollar_tag: str | None = None
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line:
            buf.append(ch)
            if ch == "\n":
                in_line = False
            i += 1
            continue
        if in_block:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                if nxt == '"':
                    buf.append(nxt)
                    i += 2
                    continue
                in_double = False
            i += 1
            continue
        if dollar_tag is not None:
            if ch == "$" and sql[i : i + len(dollar_tag)] == dollar_tag:
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            in_line = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_sql_file(operations: op, sql_path: Path) -> None:
    sql = Path(sql_path).read_text()
    for stmt in split_sql_statements(sql):
        operations.execute(text(stmt))
