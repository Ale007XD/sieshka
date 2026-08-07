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
    async def test_produces_header_and_rows(self) -> None:
        rows = [
            _row({
                "sku": "coffee", "delta": -2, "reason": "SALE",
                "source_type": "order", "source_id": "order-1",
                "below_zero": False, "created_at": datetime(2026, 8, 1, 12, 0, 0),
            }),
        ]
        service = InventoryService(session_factory=_make_session_factory(rows))

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert lines[0] == "sku,delta,reason,source_type,source_id,below_zero,created_at"
        assert lines[1] == "coffee,-2,SALE,order,order-1,False,2026-08-01T12:00:00"

    async def test_null_source_fields_become_empty_string(self) -> None:
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
        assert lines[1] == "coffee,5,RESTOCK_MANUAL,,,False,2026-08-01T09:00:00"

    async def test_empty_period_returns_header_only(self) -> None:
        service = InventoryService(session_factory=_make_session_factory([]))

        csv_text = await service.export_movements_csv()

        lines = csv_text.strip().splitlines()
        assert len(lines) == 1
        assert lines[0] == "sku,delta,reason,source_type,source_id,below_zero,created_at"
