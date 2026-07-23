"""Сидер: заполняет БД демо-меню для локальной разработки.

Создаёт категории через SQL INSERT, затем грузит CSV через /admin/menu/import-csv
(как админ-панель) для товаров с изображениями.
"""
import asyncio
import sys

sys.path.insert(0, "app")

from sqlalchemy import text

from app.db import async_session_factory

DEMO_CATEGORIES = [
    # (external_id, name, menu_period, sort)
    ("1", "Завтраки",      "morning", 10),
    ("2", "Бургеры",       "both",    20),
    ("3", "Салаты",        "both",    30),
    ("4", "Напитки",       "both",    40),
    ("5", "Десерты",       "both",    50),
    ("6", "Соусы",         "both",    60),
]

DEMO_PRODUCTS_CSV = """Name,Category,Description,Price Rub,Photo Url
Сырники с изюмом,1,Классические сырники с изюмом и сметаной,320,
Омлет с овощами,1,Воздушный омлет с томатами и зеленью,280,
Греческий салат,3,Свежие овощи с фетой и оливками,350,
Цезарь с курицей,3,Куриная грудка, листья, пармезан,390,
Бургер Классик,2,Котлета 150г, сыр чеддер, соус,320,
Чизбургер Двойной,2,Две котлеты, двойной сыр,410,
Капучино,4,Ароматный капучино на молоке,180,
Латте,4,Нежный латте с молочной пеной,190,
Чай чёрный,4,Чёрный чай с сахаром или без,120,
Чикен-бургер,2,Куриная котлета, салат, соус,300,
Картофель фри,2,Хрустящая картошка с солью,160,
Кола,4,Освежающий напиток 0.5л,140,
Тирамису,5,Итальянский десерт с маскарпоне,290,
Чизкейк,5,Классический чизкейк с ягодным соусом,310,
Кетчуп,6,Томатный соус,40,
Сырный соус,6,Нежный сырный соус,60,
"""


async def seed_categories():
    async with async_session_factory() as s:
        # Очищаем старые категории (если есть пустые)
        existing = await s.execute(text("SELECT COUNT(*) FROM categories"))
        if existing.scalar() > 0:
            print("Категории уже есть — пропускаем сид категорий")
            return {}

        # Вставляем категории
        ext_to_id = {}
        for ext_id, name, period, sort in DEMO_CATEGORIES:
            r = await s.execute(
                text(
                    "INSERT INTO categories (external_id, name, menu_period, sort, is_active) "
                    "VALUES (:ext, :name, :period, :sort, TRUE) RETURNING id"
                ),
                {"ext": ext_id, "name": name, "period": period, "sort": sort},
            )
            cat_id = r.scalar()
            ext_to_id[ext_id] = cat_id
            print(f"  Категория {name!r} (ext={ext_id}) → {cat_id}")
        await s.commit()
        return ext_to_id


async def import_csv():
    from app.services.menu_import_service import MenuImportService
    svc = MenuImportService()
    report = await svc.import_csv(DEMO_PRODUCTS_CSV.encode("utf-8"))
    print(f"\nИмпорт CSV: imported={report.imported}, skipped={len(report.skipped)}")
    for skip in report.skipped[:10]:
        print(f"  пропущено: {skip}")
    return report


async def main():
    print("=== Сидер демо-меню ===\n")
    await seed_categories()
    print("\n--- Импорт товаров (CSV через MenuImportService) ---")
    await import_csv()
    print("\nГотово. Открой / в браузере — товары должны появиться.")


if __name__ == "__main__":
    asyncio.run(main())
