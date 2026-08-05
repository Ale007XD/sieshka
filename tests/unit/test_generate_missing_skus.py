"""tests/unit/test_generate_missing_skus.py — MenuImportService.generate_missing_skus,
mocked session (no Postgres needed). sprint_inventory_menu_sync follow-up (2026-08),
extended with legacy-adoption fix (duplicate-row incident, see DECISIONS.md)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.services.menu_import_service import MenuImportService


def _row(mapping: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(_mapping=mapping)


def _make_session_factory(
    taken_skus: list[str],
    missing_sku_rows: list[dict[str, object]],
    inventory_rows: list[dict[str, object]] | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Returns (session_factory, session) — session.execute is programmed with
    side_effect matching generate_missing_skus' exact call order: (1) SELECT
    existing skus, (2) SELECT sku,name FROM inventory (legacy-adoption lookup),
    (3) SELECT products missing sku, then (4..) one UPDATE per missing row
    (return value unused, so a plain Mock is enough for those)."""
    session = AsyncMock()

    existing_result = MagicMock()
    existing_result.fetchall.return_value = [_row({"sku": s}) for s in taken_skus]

    inventory_result = MagicMock()
    inventory_result.fetchall.return_value = [_row(r) for r in (inventory_rows or [])]

    targets_result = MagicMock()
    targets_result.fetchall.return_value = [_row(r) for r in missing_sku_rows]

    update_results = [MagicMock() for _ in missing_sku_rows]

    session.execute = AsyncMock(
        side_effect=[existing_result, inventory_result, targets_result, *update_results]
    )
    session.commit = AsyncMock()

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_factory, session


class TestGenerateMissingSkus:
    async def test_assigns_slug_to_single_product(self) -> None:
        product_id = uuid4()
        session_factory, session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[{"id": product_id, "name": "Classic Burger"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.generated == 1
        assert result.adopted_legacy == 0
        assert result.assigned == [
            {"product_id": str(product_id), "name": "Classic Burger", "sku": "CLASSIC-BURGER"}
        ]
        session.commit.assert_awaited_once()

    async def test_collision_gets_suffix(self) -> None:
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=["CLASSIC-BURGER", "CLASSIC-BURGER-2"],
            missing_sku_rows=[{"id": product_id, "name": "Classic Burger"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.assigned[0]["sku"] == "CLASSIC-BURGER-3"

    async def test_within_batch_collision_gets_distinct_suffixes(self) -> None:
        id_a, id_b = uuid4(), uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[
                {"id": id_a, "name": "Coffee"},
                {"id": id_b, "name": "Coffee"},
            ],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        skus = [a["sku"] for a in result.assigned]
        assert skus == ["COFFEE", "COFFEE-2"]

    async def test_non_ascii_name_falls_back_to_item_id(self) -> None:
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[{"id": product_id, "name": "Борщ"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        expected_prefix = f"ITEM-{str(product_id)[:8].upper()}"
        assert result.assigned[0]["sku"] == expected_prefix

    async def test_no_missing_skus_is_noop(self) -> None:
        session_factory, session = _make_session_factory(
            taken_skus=["EXISTING-1"], missing_sku_rows=[],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.generated == 0
        assert result.assigned == []
        session.commit.assert_awaited_once()


class TestGenerateMissingSkusLegacyAdoption:
    """2026-08 duplicate-row fix — see DECISIONS.md. Non-ASCII product names
    (e.g. Cyrillic) with a matching pre-existing inventory row (hand-picked
    sku, predating products.sku) must adopt that sku instead of minting a
    disconnected ITEM-<id> one."""

    async def test_adopts_legacy_sku_by_case_insensitive_name_match(self) -> None:
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[{"id": product_id, "name": "Бургер Фирменный"}],
            inventory_rows=[
                {"sku": "burger-firm", "name": "Бургер Фирменный"},
            ],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.assigned[0]["sku"] == "burger-firm"
        assert result.generated == 1
        assert result.adopted_legacy == 1

    async def test_name_match_is_case_insensitive_and_trims_whitespace(self) -> None:
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[{"id": product_id, "name": "  shaurma barbeku  "}],
            inventory_rows=[{"sku": "shaurma-bbq", "name": "SHAURMA BARBEKU"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.assigned[0]["sku"] == "shaurma-bbq"

    async def test_legacy_sku_already_claimed_by_another_product_falls_back_to_slug(
        self,
    ) -> None:
        # inventory row exists by name, but its sku is already assigned to a
        # DIFFERENT product (products.sku already contains it) — must not
        # create a second product pointing at the same sku (UNIQUE violation).
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=["burger-firm"],  # already claimed by another product
            missing_sku_rows=[{"id": product_id, "name": "Бургер Фирменный"}],
            inventory_rows=[{"sku": "burger-firm", "name": "Бургер Фирменный"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.assigned[0]["sku"] != "burger-firm"
        assert result.assigned[0]["sku"] == "ITEM-" + str(product_id)[:8].upper()
        assert result.adopted_legacy == 0

    async def test_no_matching_inventory_row_falls_back_to_slug(self) -> None:
        product_id = uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[{"id": product_id, "name": "Classic Burger"}],
            inventory_rows=[{"sku": "unrelated-sku", "name": "Some Other Item"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.assigned[0]["sku"] == "CLASSIC-BURGER"
        assert result.adopted_legacy == 0

    async def test_mixed_batch_some_adopted_some_slugified(self) -> None:
        id_legacy, id_new = uuid4(), uuid4()
        session_factory, _session = _make_session_factory(
            taken_skus=[],
            missing_sku_rows=[
                {"id": id_legacy, "name": "Шаурма Барбекю"},
                {"id": id_new, "name": "New Item"},
            ],
            inventory_rows=[{"sku": "shaurma-bbq", "name": "Шаурма Барбекю"}],
        )
        service = MenuImportService(session_factory=session_factory)

        result = await service.generate_missing_skus()

        assert result.generated == 2
        assert result.adopted_legacy == 1
        by_id = {a["product_id"]: a["sku"] for a in result.assigned}
        assert by_id[str(id_legacy)] == "shaurma-bbq"
        assert by_id[str(id_new)] == "NEW-ITEM"
