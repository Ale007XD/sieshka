"""tests/unit/test_inventory_stats.py — InventoryService.movements_summary +
export_movements_csv, mocked session (no Postgres needed).
sprint_inventory_stats_viz (2026-08)."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.inventory_service import InventoryService


def _row(mapping: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(_mapping=mapping)


def _make_session_factory(fetchall_return: list[SimpleNamespace]) -> MagicMock:
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = fetchall_return
    session.execute = AsyncMock(return_value=result)

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_factory


def _make_multi_query_session_factory(
    *query_results: list[SimpleNamespace],
) -> MagicMock:
    """For export_movements_csv's up-to-3-query sequence: movements, then
    (only if skus non-empty) products sku->id, then (only if any SALE rows
    with a source_id) orders. Pass exactly as many result-lists as the test
    scenario will actually trigger queries for."""
    session = AsyncMock()
    results = []
    for rows in query_results:
        result = MagicMock()
        result.fetchall.return_value = rows
        results.append(result)
    session.execute = AsyncMock(side_effect=results)

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_factory


class TestMovementsSummary:
    async def test_builds_labels_and_series_from_rows(self) -> None:
        rows = [
            _row({"day": date(2026, 8, 1), "in_qty": 50, "out_qty": 3}),
            _row({"day": date(2026, 8, 2), "in_qty": 0, "out_qty": 12}),
        ]
        service = InventoryService(session_factory=_make_session_factory(rows))

        summary = await service.movements_summary()

        assert summary.labels == ["2026-08-01", "2026-08-02"]
        assert summary.restocked == [50, 0]
        assert summary.sold == [3, 12]

    async def test_empty_period_returns_empty_series(self) -> None:
        service = InventoryService(session_factory=_make_session_factory([]))

        summary = await service.movements_summary(
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 31)
        )

        assert summary.labels == []
        assert summary.restocked == []
        assert summary.sold == []

    async def test_passes_date_bounds_as_query_params(self) -> None:
        session_factory = _make_session_factory([])
        service = InventoryService(session_factory=session_factory)
        from_d, to_d = date(2026, 8, 1), date(2026, 8, 7)

        await service.movements_summary(from_date=from_d, to_date=to_d)

        session = session_factory.return_value.__aenter__.return_value
        params = session.execute.await_args.args[1]
        assert params == {"from_date": from_d, "to_date": to_d}


class TestExportMovementsCsv:
    async def test_no_sale_rows_no_extra_queries_no_price_columns(self) -> None:
        """RESTOCK/ADJUSTMENT-only export never touches products/orders —
        only the movements query runs (single-result mock proves this: if
        the code tried a second query, it would get the movements rows
        again, shaped wrong, and crash)."""
        rows = [
            _row({
                "sku": "coffee", "delta": 5, "reason": "RESTOCK_MANUAL",
                "source_type": None, "source_id": None,
                "below_zero": False, "created_at": datetime(2026, 8, 1, 9, 0, 0),
            }),
        ]
        service = InventoryService(session_factory=_make_session_factory(rows))

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[0] == (
            "sku,delta,reason,source_type,source_id,below_zero,created_at,"
            "price_rub,sale_amount"
        )
        assert lines[1] == "coffee,5,RESTOCK_MANUAL,,,False,2026-08-01T09:00:00,,"

    async def test_empty_period_returns_header_and_empty_summary(self) -> None:
        service = InventoryService(session_factory=_make_session_factory([]))

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[0] == (
            "sku,delta,reason,source_type,source_id,below_zero,created_at,"
            "price_rub,sale_amount"
        )
        assert "Daily sales totals" in lines
        assert lines[-1] == "Grand total,0"

    async def test_sale_row_resolves_price_from_order_snapshot(self) -> None:
        movement_rows = [
            _row({
                "sku": "coffee", "delta": -2, "reason": "SALE",
                "source_type": "order", "source_id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 12, 0, 0),
            }),
        ]
        product_rows = [_row({"id": "prod-1", "sku": "coffee"})]
        order_rows = [
            _row({
                "id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "items": [{"product_id": "prod-1", "name": "Coffee", "price_rub": 150, "qty": 2}],
            }),
        ]
        service = InventoryService(
            session_factory=_make_multi_query_session_factory(
                movement_rows, product_rows, order_rows,
            )
        )

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[1] == (
            "coffee,-2,SALE,order,b438e086-23b1-4520-993c-63e11acd3ab9,False,"
            "2026-08-07T12:00:00,150,300"
        )

    async def test_sale_row_items_as_json_string_still_parses(self) -> None:
        """orders.items is JSONB — asyncpg usually pre-decodes it, but the
        code defensively handles a raw JSON string too (same pattern as
        order_tools.py::reserve_inventory_items)."""
        movement_rows = [
            _row({
                "sku": "coffee", "delta": -1, "reason": "SALE",
                "source_type": "order", "source_id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 12, 0, 0),
            }),
        ]
        product_rows = [_row({"id": "prod-1", "sku": "coffee"})]
        order_rows = [
            _row({
                "id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "items": '[{"product_id": "prod-1", "price_rub": 150, "qty": 1}]',
            }),
        ]
        service = InventoryService(
            session_factory=_make_multi_query_session_factory(
                movement_rows, product_rows, order_rows,
            )
        )

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert ",150,150" in lines[1]

    async def test_sale_row_no_matching_order_item_leaves_price_empty(self) -> None:
        movement_rows = [
            _row({
                "sku": "coffee", "delta": -1, "reason": "SALE",
                "source_type": "order", "source_id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 12, 0, 0),
            }),
        ]
        product_rows = [_row({"id": "prod-1", "sku": "coffee"})]
        order_rows = [
            _row({
                "id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "items": [{"product_id": "some-other-product", "price_rub": 99, "qty": 1}],
            }),
        ]
        service = InventoryService(
            session_factory=_make_multi_query_session_factory(
                movement_rows, product_rows, order_rows,
            )
        )

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[1].endswith(",False,2026-08-07T12:00:00,,")

    async def test_invalid_source_id_skips_orders_query_gracefully(self) -> None:
        movement_rows = [
            _row({
                "sku": "coffee", "delta": -1, "reason": "SALE",
                "source_type": "order", "source_id": "not-a-uuid",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 12, 0, 0),
            }),
        ]
        product_rows = [_row({"id": "prod-1", "sku": "coffee"})]
        # only 2 results provided — proves the orders query never fires
        service = InventoryService(
            session_factory=_make_multi_query_session_factory(movement_rows, product_rows)
        )

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[1].endswith(",False,2026-08-07T12:00:00,,")

    async def test_daily_totals_and_grand_total(self) -> None:
        movement_rows = [
            _row({
                "sku": "coffee", "delta": -2, "reason": "SALE",
                "source_type": "order", "source_id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 9, 0, 0),
            }),
            _row({
                "sku": "coffee", "delta": -1, "reason": "SALE",
                "source_type": "order", "source_id": "9195df49-1011-40b2-9ddb-96d8402997e5",
                "below_zero": False, "created_at": datetime(2026, 8, 7, 15, 0, 0),
            }),
            _row({
                "sku": "coffee", "delta": -3, "reason": "SALE",
                "source_type": "order", "source_id": "9195df49-1011-40b2-9ddb-96d8402997e6",
                "below_zero": False, "created_at": datetime(2026, 8, 8, 9, 0, 0),
            }),
        ]
        product_rows = [_row({"id": "prod-1", "sku": "coffee"})]
        order_rows = [
            _row({
                "id": "b438e086-23b1-4520-993c-63e11acd3ab9",
                "items": [{"product_id": "prod-1", "price_rub": 100, "qty": 2}],
            }),
            _row({
                "id": "9195df49-1011-40b2-9ddb-96d8402997e5",
                "items": [{"product_id": "prod-1", "price_rub": 100, "qty": 1}],
            }),
            _row({
                "id": "9195df49-1011-40b2-9ddb-96d8402997e6",
                "items": [{"product_id": "prod-1", "price_rub": 100, "qty": 3}],
            }),
        ]
        service = InventoryService(
            session_factory=_make_multi_query_session_factory(
                movement_rows, product_rows, order_rows,
            )
        )

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        # 3 detail rows + header = 4, then blank + "Daily sales totals" + header + 2 days
        # + blank + grand total
        assert "Daily sales totals" in lines
        totals_idx = lines.index("Daily sales totals")
        assert lines[totals_idx + 1] == "date,sale_amount"
        assert lines[totals_idx + 2] == "2026-08-07,300"  # 200 + 100
        assert lines[totals_idx + 3] == "2026-08-08,300"
        assert lines[-1] == "Grand total,600"

    async def test_no_sales_still_produces_empty_summary_sections(self) -> None:
        rows = [
            _row({
                "sku": "coffee", "delta": 5, "reason": "RESTOCK_MANUAL",
                "source_type": None, "source_id": None,
                "below_zero": False, "created_at": datetime(2026, 8, 1, 9, 0, 0),
            }),
        ]
        service = InventoryService(session_factory=_make_session_factory(rows))

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert "Daily sales totals" in lines
        assert lines[-1] == "Grand total,0"


class TestSqlBindParamNamesMatchPassedParams:
    """Fast, no-Postgres-needed regression guard for a real bug class:
    SQLAlchemy's text() bind-parameter parser misreads `:name::type` cast
    syntax (the `:` in `::` starts a NEW bind expression and the regex
    truncates the parameter name — confirmed empirically:
    `text("...:from_date::date...")._bindparams` resolves to a key named
    `from_dat`, not `from_date`). That mismatch against the params dict
    passed to session.execute() 500s on every call, not just the None case —
    exactly what shipped and broke sprint_inventory_stats_viz twice (2026-08).
    See tests/integration/test_inventory_stats_integration.py for the
    live-Postgres version of this guard; this one runs unconditionally,
    every CI run, no Docker needed — it inspects the compiled TextClause
    SQLAlchemy actually receives, without executing it."""

    async def test_movements_summary_bind_params_match_passed_dict(self) -> None:
        captured: dict[str, object] = {}

        async def _capture_execute(query: object, params: dict[str, object]) -> MagicMock:
            captured["query"] = query
            captured["params"] = params
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        session = AsyncMock()
        session.execute = _capture_execute
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        service = InventoryService(session_factory=session_factory)

        await service.movements_summary(from_date=date(2026, 8, 1), to_date=date(2026, 8, 7))

        query = captured["query"]
        params = captured["params"]
        assert isinstance(params, dict)
        bind_names = set(query._bindparams.keys())  # type: ignore[attr-defined]
        assert bind_names == set(params.keys()), (
            f"SQL bind param names {bind_names} don't match the params dict "
            f"keys {set(params.keys())} — SQLAlchemy will raise at execute "
            f"time regardless of what values are passed"
        )

    async def test_export_movements_csv_bind_params_match_passed_dict(self) -> None:
        captured: dict[str, object] = {}

        async def _capture_execute(query: object, params: dict[str, object]) -> MagicMock:
            captured["query"] = query
            captured["params"] = params
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        session = AsyncMock()
        session.execute = _capture_execute
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        service = InventoryService(session_factory=session_factory)

        await service.export_movements_csv(from_date=date(2026, 8, 1), to_date=date(2026, 8, 7))

        query = captured["query"]
        params = captured["params"]
        assert isinstance(params, dict)
        bind_names = set(query._bindparams.keys())  # type: ignore[attr-defined]
        assert bind_names == set(params.keys())
