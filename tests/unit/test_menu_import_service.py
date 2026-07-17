"""tests/unit/test_menu_import_service.py — parse_and_validate_csv behaviour.

No DB: categories + existing products are passed in directly, mirroring what
MenuImportService loads and hands to the parser. One test per documented
skip-reason plus the happy-path / update / blank-price cases.
"""
from __future__ import annotations

from uuid import uuid4

from app.domains.menu.models import Category, Product
from app.services.menu_import_service import (
    ImportReport,
    SkippedRow,
    parse_and_validate_csv,
)


def _cat(external_id: str | None, name: str) -> Category:
    return Category(
        id=uuid4(),
        external_id=external_id,
        name=name,
        parent_name=None,
        menu_period="both",
        sort=10,
        is_active=True,
    )


def _prod(name: str) -> Product:
    return Product(
        id=uuid4(),
        name=name,
        category_id=None,
        menu_period_override=None,
        price_rub=100,
        description=None,
        image_url=None,
        is_active=True,
    )


CATEGORIES = [
    _cat("1", "Бургеры"),
    _cat("8", "!!!КОМБО!!!"),  # seeded WITHOUT spaces
    _cat("19", "Морс"),
    _cat("21", "Вода"),
]

EMPTY_PRODUCTS: list[Product] = []


# ---------------------------------------------------------------------------
# Skip reasons
# ---------------------------------------------------------------------------


def test_missing_name_is_skipped() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\n,Бургеры,,350,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert valid == []
    assert len(skipped) == 1
    assert skipped[0].reason == "missing name"
    assert skipped[0].name_if_present is None


def test_unknown_category_is_skipped() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,Несуществует,,350,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert valid == []
    assert skipped[0].reason == "unknown category: Несуществует"
    assert skipped[0].name_if_present == "Burger"


def test_unknown_category_by_int_id_is_skipped() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,999,,350,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert valid == []
    assert skipped[0].reason == "unknown category: 999"


def test_invalid_price_is_skipped() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,Бургеры,,abc,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert valid == []
    assert skipped[0].reason == "invalid price"


def test_negative_price_is_skipped() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,Бургеры,,-5,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert valid == []
    assert skipped[0].reason == "invalid price"


def test_ambiguous_name_match_is_skipped() -> None:
    dup = "Одинаковое"
    products = [_prod(dup), _prod(dup)]
    csv = f"Name,Category,Description,Price Rub,Photo Url\n{dup},Бургеры,,350,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=products
    )
    assert valid == []
    assert skipped[0].reason == "ambiguous name match: 2 rows"
    assert skipped[0].name_if_present == dup


# ---------------------------------------------------------------------------
# Happy path / updates / blank price
# ---------------------------------------------------------------------------


def test_happy_path_multi_row_import() -> None:
    csv = (
        "Name,Category,Description,Price Rub,Photo Url\n"
        "Burger,Бургеры,tasty,350,http://img/burger.png\n"
        "Water,Вода,,50,\n"
        "Kombo,!!! КОМБО !!!,,700,\n"  # whitespace must normalize to "!!!КОМБО!!!"
    )
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert skipped == []
    assert len(valid) == 3

    by_name = {r.name: r for r in valid}
    assert by_name["Burger"].category_id == CATEGORIES[0].id
    assert by_name["Burger"].price_rub == 350
    assert by_name["Burger"].is_active is True
    assert by_name["Water"].category_id == CATEGORIES[3].id
    # Kombo resolves via whitespace-normalized name match (seeded without spaces).
    assert by_name["Kombo"].category_id == CATEGORIES[1].id


def test_blank_category_is_unassigned_not_error() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nSide,,,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert skipped == []
    assert valid[0].name == "Side"
    assert valid[0].category_id is None


def test_update_existing_by_name() -> None:
    products = [_prod("Burger")]  # existing product named Burger, price 100
    csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,Бургеры,,200,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=products
    )
    assert skipped == []
    assert len(valid) == 1
    assert valid[0].name == "Burger"
    assert valid[0].price_rub == 200
    assert valid[0].is_active is True


def test_blank_price_forces_inactive() -> None:
    csv = "Name,Category,Description,Price Rub,Photo Url\nFries,Бургеры,,,\n"
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert skipped == []
    assert len(valid) == 1
    assert valid[0].price_rub is None
    assert valid[0].is_active is False


def test_bom_and_quoted_fields_parsed() -> None:
    csv = (
        "\ufeffName,Category,Description,Price Rub,Photo Url\n"
        '"Quoted, Burger",Бургеры,"a, b",350,http://x/y.png\n'
    )
    valid, skipped = parse_and_validate_csv(
        csv.encode("utf-8"), categories=CATEGORIES, existing_products=EMPTY_PRODUCTS
    )
    assert skipped == []
    assert valid[0].name == "Quoted, Burger"
    assert valid[0].description == "a, b"


def test_import_report_model() -> None:
    report = ImportReport(
        imported=3,
        skipped=[SkippedRow(row_num=2, name_if_present="X", reason="missing name")],
        trace_hash="abc",
        final_status="success",
    )
    assert report.imported == 3
    assert report.skipped[0].reason == "missing name"
    assert report.final_status == "success"
