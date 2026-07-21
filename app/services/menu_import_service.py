"""app/services/menu_import_service.py — menu CSV import (sprint_m7_menu_csv_import).

Two distinct responsibilities, kept as separate layers on purpose:

1. parse_and_validate_csv(file_bytes, *, categories, existing_products) —
   a PLAIN, non-governed PARSING step. It turns CSV text into structured
   rows and decides, row by row, which are valid and which are skipped (with a
   reason). It writes NOTHING. Category resolution and the ambiguous-name
   check both need data that lives in the DB, so the caller hands in the
   current categories and product names; the function stays free of any
   session/IO of its own.

2. MenuImportService — the composition root. It loads categories + product
   names, calls parse_and_validate_csv, then runs MENU_IMPORT_PROGRAM through
   ExecutionVM (exactly like OrderService does for order transitions). The
   governed terminal tool (apply_menu_import) does the actual batch upsert
   inside the single session transaction the service owns.

Upsert identity is BY NAME (case-insensitive exact match) — the only identity
strategy for products in this project.
"""
from __future__ import annotations

import csv
import functools
import io
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from nano_vm.models import Trace, TraceStatus
from nano_vm.validator import ProgramValidator
from nano_vm_mcp.handlers import GovernedToolExecutor
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_factory
from app.db_nano import StoreCursorRepository, get_store
from app.domains.menu.models import Category, Product
from app.policy.policy_snapshot import MENU_IMPORT_POLICY_SNAPSHOT
from app.programs.menu_import_program import MENU_IMPORT_PROGRAM
from app.services.trace_analyzer import TraceAnalyzer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------


class ProductRow(BaseModel):
    """A single fully-validated, ready-to-upsert product row."""

    name: str
    category_id: UUID | None = None
    price_rub: int | None = None
    description: str | None = None
    image_url: str | None = None
    is_active: bool = True


class SkippedRow(BaseModel):
    """One CSV row that was skipped, with the reason it was skipped."""

    row_num: int
    name_if_present: str | None = None
    reason: str


class ProductAdminRow(BaseModel):
    """Read model for the admin product table."""

    id: str
    name: str
    category_name: str | None
    price_rub: int | None
    description: str | None = None
    image_url: str | None = None
    is_active: bool


class IncompleteCounts(BaseModel):
    """Counts of products missing the minimum to be orderable/complete."""

    null_category: int
    null_price: int


class ImportReport(BaseModel):
    """Aggregated result of one CSV import run."""

    imported: int
    skipped: list[SkippedRow]
    trace_hash: str | None = None
    final_status: str | None = None


# ---------------------------------------------------------------------------
# Parsing / validation (non-governed)
# ---------------------------------------------------------------------------


_WHITESPACE = re.compile(r"\s+")


def _normalize(value: str) -> str:
    """Case + whitespace normalization for name/category matching.

    Lower-cases and STRIPS ALL whitespace, so "!!! КОМБО !!!" matches the
    seeded "!!!КОМБО!!!" (no spaces) and vice-versa — the cosmetic formatting
    difference Alex's spec calls out explicitly. Removing (not just collapsing)
    whitespace is what makes the spaced vs. unspaced combo names match.
    """
    return _WHITESPACE.sub("", value.lower())


# Header aliases → internal field name. Keys are lowercased with spaces removed.
_HEADER_ALIASES: dict[str, str] = {
    "name": "name",
    "category": "category",
    "description": "description",
    "pricerub": "price_rub",
    "price": "price_rub",
    "photourl": "image_url",
    "photo": "image_url",
}


def parse_and_validate_csv(
    file_bytes: bytes,
    *,
    categories: Sequence[Category],
    existing_products: Sequence[Product],
) -> tuple[list[ProductRow], list[SkippedRow]]:
    """Parse CSV text into validated rows + a per-row skip report.

    `categories` and `existing_products` are read from the DB by the caller;
    this function performs no IO of its own. Any row that fails at any step is
    skipped (reason logged) and the next row is processed — the whole import
    never aborts on a single bad row.

    Skip reasons surfaced here (tested in tests/unit/test_menu_import_service):
      - "missing name"
      - "unknown category: <value>"
      - "invalid price"
      - "ambiguous name match: N rows"
    """
    # Build category lookup tables.
    by_external_id: dict[str, UUID] = {}
    by_normalized_name: dict[str, UUID] = {}
    for cat in categories:
        if cat.external_id is not None:
            by_external_id[str(cat.external_id).strip()] = cat.id
        by_normalized_name[_normalize(cat.name)] = cat.id

    # Count existing products per normalized name for the ambiguity check.
    name_counts: dict[str, int] = {}
    for prod in existing_products:
        key = _normalize(prod.name)
        name_counts[key] = name_counts.get(key, 0) + 1

    text = file_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    raw_rows = list(reader)
    if not raw_rows:
        return [], []

    header = raw_rows[0]
    col_index: dict[str, int] = {}
    for i, cell in enumerate(header):
        key = cell.strip().lower().replace(" ", "")
        field = _HEADER_ALIASES.get(key)
        if field is not None:
            col_index[field] = i

    def _get(row: list[str], field: str) -> str:
        idx = col_index.get(field)
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    valid_rows: list[ProductRow] = []
    skipped: list[SkippedRow] = []

    # In-file de-duplication: tracks normalized names already accepted from
    # THIS csv so a later same-named row is skipped instead of silently
    # overwriting the earlier row's upsert (the row-by-row apply loop would
    # otherwise let the last row in the file win). Built from valid_rows as
    # they're accumulated.
    in_file_counts: dict[str, int] = {}

    for offset, raw in enumerate(raw_rows[1:]):
        row_num = offset + 2  # 1-based; header is row 1

        def _skip(reason: str, name: str | None = None) -> None:
            skipped.append(
                SkippedRow(row_num=row_num, name_if_present=name, reason=reason)
            )

        # 1. Name required.
        name = _get(raw, "name").strip()
        if not name:
            _skip("missing name")
            continue

        # Ambiguous name match (more than one existing product with this name).
        existing_count = name_counts.get(_normalize(name), 0)
        if existing_count > 1:
            _skip(f"ambiguous name match: {existing_count} rows", name)
            continue

        # Duplicate name within this very file — a later row with a name that
        # already appeared earlier in the same upload must not silently clobber
        # the earlier row (last-write-wins).
        norm = _normalize(name)
        if in_file_counts.get(norm, 0) > 0:
            _skip("duplicate name within this file", name)
            continue

        # 2. Category resolution.
        category_raw = _get(raw, "category").strip()
        category_id: UUID | None = None
        if category_raw == "":
            # Blank/omitted → unassigned. NOT an error.
            category_id = None
        else:
            resolved: UUID | None = None
            try:
                as_int = int(category_raw)
                resolved = by_external_id.get(str(as_int))
            except ValueError:
                resolved = None
            if resolved is None:
                resolved = by_normalized_name.get(_normalize(category_raw))
            if resolved is None:
                _skip(f"unknown category: {category_raw}", name)
                continue
            category_id = resolved

        # 3. Price Rub.
        price_raw = _get(raw, "price_rub").strip()
        price_rub: int | None = None
        if price_raw == "":
            # Blank/omitted → unpriced + forced inactive (degrade safely).
            price_rub = None
        else:
            try:
                num = float(price_raw)
            except ValueError:
                _skip("invalid price", name)
                continue
            if num < 0:
                _skip("invalid price", name)
                continue
            price_rub = int(num)

        # An unpriced product must never be orderable.
        is_active = price_rub is not None

        # 4. Description, Photo Url — pass through as-is, None if blank.
        description = _get(raw, "description").strip() or None
        image_url = _get(raw, "image_url").strip() or None

        valid_rows.append(
            ProductRow(
                name=name,
                category_id=category_id,
                price_rub=price_rub,
                description=description,
                image_url=image_url,
                is_active=is_active,
            )
        )
        in_file_counts[norm] = in_file_counts.get(norm, 0) + 1

    return valid_rows, skipped


# ---------------------------------------------------------------------------
# Composition root — runs the governed Program through ExecutionVM
# ---------------------------------------------------------------------------


def _governed_tool(
    fn: Callable[..., Any],
    tool_name: str,
    executor: Any,
) -> Callable[..., Any]:
    async def wrapper(**kwargs: object) -> Any:
        executor.check(tool_name)
        return await fn(**kwargs)

    return wrapper


_MENU_IMPORT_TOOL = "apply_menu_import"


def _build_vm(session: AsyncSession) -> Any:
    """Build an ExecutionVM bound to `session`, mirroring OrderService._build_vm.

    The write tool is registered with the session closure-injected (never
    serialised in the Trace), and wrapped by GovernedToolExecutor so
    menu:write capability is enforced on every run.
    """
    from nano_vm.adapters import MockLLMAdapter
    from nano_vm.vm import ExecutionVM

    from app.tools.menu_import_tools import apply_menu_import

    cursor = StoreCursorRepository(get_store())
    vm = ExecutionVM(
        llm=MockLLMAdapter(""),
        cursor_repository=cursor,
    )
    executor = GovernedToolExecutor(MENU_IMPORT_POLICY_SNAPSHOT)
    governed = _governed_tool(apply_menu_import, _MENU_IMPORT_TOOL, executor)
    vm.register_tool(
        _MENU_IMPORT_TOOL,
        functools.partial(governed, session=session),
    )
    return vm


class MenuImportService:
    """Composition root for the menu CSV import flow."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        vm: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vm = vm

    def _transition_vm(self, session: AsyncSession) -> Any:
        if self._vm is not None:
            return self._vm
        return _build_vm(session)

    async def import_csv(self, file_bytes: bytes) -> ImportReport:
        """Parse + run the governed import Program for one uploaded CSV file."""
        async with self._session_factory() as session:
            categories = await self._load_categories(session)
            existing = await self._load_product_names(session)

            valid_rows, skipped = parse_and_validate_csv(
                file_bytes,
                categories=categories,
                existing_products=existing,
            )

            vm = self._transition_vm(session)
            report = ProgramValidator(MENU_IMPORT_PROGRAM).validate()
            if not report.is_valid():
                raise RuntimeError(
                    f"Program '{MENU_IMPORT_PROGRAM.name}' validation failed: "
                    f"{report.summary()}"
                )

            trace: Trace = await vm.run(
                MENU_IMPORT_PROGRAM,
                # BUGFIX (2026-07-15): mode="json" is required here — plain
                # model_dump() leaves category_id as a uuid.UUID instance, and
                # apply_menu_import's UUID(category_id_raw) call then raises
                # (UUID() rejects a UUID instance, it wants a hex string/bytes).
                # This broke every row with a non-null category_id, not just an
                # edge case — Trace.status went FAILED on any real import.
                context={"valid_rows": [r.model_dump(mode="json") for r in valid_rows]},
            )

            imported = 0
            write_skipped: list[SkippedRow] = []
            if trace.status == TraceStatus.SUCCESS:
                last = trace.last_output()
                if isinstance(last, dict):
                    imported = int(last.get("imported", 0))
                    for ws in last.get("write_skipped", []) or []:
                        write_skipped.append(
                            SkippedRow(
                                row_num=0,
                                name_if_present=ws.get("name"),
                                reason=ws.get("reason", "skipped at write time"),
                            )
                        )
                await session.commit()
            else:
                await session.rollback()
                logger.error(
                    "menu_import: program failed — %s", trace.error or "unknown"
                )

            # ONE unified skip list: parse-time skips + apply-time write skips.
            receipt = TraceAnalyzer._build_receipt(trace)
            return ImportReport(
                imported=imported,
                skipped=skipped + write_skipped,
                trace_hash=receipt.trace_hash,
                final_status=receipt.final_status,
            )

    async def get_admin_data(
        self,
    ) -> tuple[list[ProductAdminRow], IncompleteCounts]:
        """Load all products (with category name) + incomplete-product counts."""
        async with self._session_factory() as session:
            products = await self._load_product_admin_rows(session)
            counts = await self._load_incomplete_counts(session)
            return products, counts

    # ------------------------------------------------------------------
    # DB reads
    # ------------------------------------------------------------------

    async def _load_categories(self, session: AsyncSession) -> list[Category]:
        # BUGFIX (2026-07-15): categories has parent_category_id UUID (self-FK,
        # migrations/004_menu.sql), NOT a parent_name column. The Category model's
        # parent_name field is a resolved display string for the flat admin
        # dropdown — resolve it here via a self-join, don't query a column that
        # was never created.
        result = await session.execute(
            text(
                "SELECT c.id, c.external_id, c.name, p.name AS parent_name, "
                "c.menu_period, c.sort, c.is_active "
                "FROM categories c "
                "LEFT JOIN categories p ON p.id = c.parent_category_id"
            )
        )
        rows = result.fetchall()
        return [
            Category(
                id=row._mapping["id"],
                external_id=row._mapping["external_id"],
                name=row._mapping["name"],
                parent_name=row._mapping["parent_name"],
                menu_period=row._mapping["menu_period"],
                sort=row._mapping["sort"],
                is_active=row._mapping["is_active"],
            )
            for row in rows
        ]

    async def _load_product_names(self, session: AsyncSession) -> list[Product]:
        result = await session.execute(
            text("SELECT id, name, category_id, menu_period_override, "
                 "price_rub, description, image_url, is_active FROM products")
        )
        rows = result.fetchall()
        return [
            Product(
                id=row._mapping["id"],
                name=row._mapping["name"],
                category_id=row._mapping["category_id"],
                menu_period_override=row._mapping["menu_period_override"],
                price_rub=row._mapping["price_rub"],
                description=row._mapping["description"],
                image_url=row._mapping["image_url"],
                is_active=row._mapping["is_active"],
            )
            for row in rows
        ]

    async def _load_product_admin_rows(
        self, session: AsyncSession
    ) -> list[ProductAdminRow]:
        result = await session.execute(
            text(
                "SELECT p.id AS id, p.name AS name, c.name AS category_name, "
                "p.price_rub AS price_rub, p.description AS description, "
                "p.image_url AS image_url, p.is_active AS is_active "
                "FROM products p "
                "LEFT JOIN categories c ON p.category_id = c.id "
                "ORDER BY p.name"
            )
        )
        rows = result.fetchall()
        return [
            ProductAdminRow(
                id=str(row._mapping["id"]),
                name=row._mapping["name"],
                category_name=row._mapping["category_name"],
                price_rub=row._mapping["price_rub"],
                description=row._mapping["description"],
                image_url=row._mapping["image_url"],
                is_active=row._mapping["is_active"],
            )
            for row in rows
        ]

    async def _load_incomplete_counts(
        self, session: AsyncSession
    ) -> IncompleteCounts:
        result = await session.execute(
            text(
                "SELECT "
                "COUNT(*) FILTER (WHERE category_id IS NULL) AS null_category, "
                "COUNT(*) FILTER (WHERE price_rub IS NULL) AS null_price "
                "FROM products"
            )
        )
        row = result.one()
        return IncompleteCounts(
            null_category=int(row._mapping["null_category"] or 0),
            null_price=int(row._mapping["null_price"] or 0),
        )
