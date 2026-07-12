"""tests/integration/test_menu_repo.py — httpx AsyncClient against menu API.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.menu import get_menu_service
from app.api.routes.menu import router as menu_router
from app.services.menu_service import MenuService


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_paths = [
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "004_menu.sql",
    ]
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        for sp in schema_paths:
            schema = sp.read_text()
            await conn.execute(schema)
        # products.category_id → categories(id) FK — CASCADE обязателен,
        # иначе TRUNCATE categories падает на "referenced by table products"
        await conn.execute("TRUNCATE TABLE products, categories CASCADE")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def seed_categories(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Load the 24-row category dataset for test."""
    data_path = Path(__file__).resolve().parents[2] / "data" / "categories.json"
    categories = json.loads(data_path.read_text(encoding="utf-8"))

    async with session_factory() as session:
        for cat in categories:
            await session.execute(
                text(
                    "INSERT INTO categories (external_id, name, menu_period, sort, is_active) "
                    "VALUES (:external_id, :name, :menu_period, :sort, :is_active)"
                ),
                {
                    "external_id": str(cat["id"]),
                    "name": cat["name"],
                    "menu_period": cat["menu_period"],
                    "sort": cat["sort"],
                    "is_active": cat["is_active"],
                },
            )
        await session.commit()

    name_to_id: dict[str, str] = {}
    async with session_factory() as session:
        rows = await session.execute(text("SELECT id, name FROM categories"))
        for row in rows.mappings():
            name_to_id[row["name"]] = str(row["id"])

    async with session_factory() as session:
        for cat in categories:
            parent_name = cat.get("parent")
            if parent_name:
                parent_id = name_to_id.get(parent_name)
                if parent_id:
                    await session.execute(
                        text(
                            "UPDATE categories SET parent_category_id = :parent_id "
                            "WHERE name = :name"
                        ),
                        {"parent_id": parent_id, "name": cat["name"]},
                    )
        await session.commit()


@pytest.fixture
async def seed_products(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Insert a few test products."""
    async with session_factory() as session:
        cat_result = await session.execute(
            text("SELECT id FROM categories WHERE name = :name LIMIT 1"),
            {"name": "Бургеры"},
        )
        cat_row = cat_result.mappings().first()
        if not cat_row:
            return

        cat_id = cat_row["id"]
        for name, price in (
            ("Чизбургер", 199),
            ("Гамбургер", 149),
            ("Скрытый товар", None),
        ):
            await session.execute(
                text(
                    "INSERT INTO products (name, category_id, price_rub, is_active) "
                    "VALUES (:name, :category_id, :price_rub, :is_active)"
                ),
                {"name": name, "category_id": cat_id, "price_rub": price, "is_active": True},
            )
        await session.commit()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(menu_router)

    async def _test_service() -> MenuService:
        return MenuService(session_factory=session_factory)

    app.dependency_overrides[get_menu_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestMenuAPI:
    async def test_get_menu_returns_structure(
        self,
        client: AsyncClient,
        seed_categories: None,
        seed_products: None,
    ) -> None:
        resp = await client.get("/api/menu", params={"method": "delivery"})
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert isinstance(data["categories"], list)

        # Only active categories with matching period should appear
        category_names = {c["name"] for c in data["categories"]}
        assert "Бургеры" in category_names
        assert "Салаты" not in category_names  # is_active = False

        # Бургеры should have products
        burgers = next(c for c in data["categories"] if c["name"] == "Бургеры")
        assert len(burgers["products"]) == 2  # скрытый товар excluded (no price)
        product_names = {p["name"] for p in burgers["products"]}
        assert "Чизбургер" in product_names
        assert "Гамбургер" in product_names

        # Verify product shape
        burger = next(p for p in burgers["products"] if p["name"] == "Чизбургер")
        assert burger["price_rub"] == 199
        assert burger["available"] is True
        assert burger["cta_type"] == "add_to_cart"
        assert burger["reason_code"] is None

    async def test_delivery_fee(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/config/delivery-fee")
        assert resp.status_code == 200
        data = resp.json()
        assert "delivery_fee" in data
        assert isinstance(data["delivery_fee"], int)

    async def test_get_menu_empty_no_categories(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/menu")
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == []
