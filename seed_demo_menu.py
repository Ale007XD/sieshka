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
Сырники с изюмом,1,Классические сырники с изюмом и сметаной,320,https://images.unsplash.com/photo-1517673400267-02547b56a537?w=400&h=300&fit=crop
Омлет с овощами,1,Воздушный омлет с томатами и зеленью,280,https://images.unsplash.com/photo-1525351484163-7529414344d8?w=400&h=300&fit=crop
Греческий салат,3,Свежие овощи с фетой и оливками,350,https://images.unsplash.com/photo-1540189549336-e6e99c3679fe?w=400&h=300&fit=crop
Цезарь с курицей,3,Куриная грудка, листья, пармезан,390,https://images.unsplash.com/photo-1546793665-c74683f339c1?w=400&h=300&fit=crop
Бургер Классик,2,Котлета 150г, сыр чеддер, соус,320,https://images.unsplash.com/photo-1568901346375-23c91905c8cd?w=400&h=300&fit=crop
Чизбургер Двойной,2,Две котлеты, двойной сыр,410,https://images.unsplash.com/photo-1553979459-d2229ba7433a?w=400&h=300&fit=crop
Капучино,4,Ароматный капучино на молоке,180,https://images.unsplash.com/photo-1534778101976-62847782c213?w=400&h=300&fit=crop
Латте,4,Нежный латте с молочной пеной,190,https://images.unsplash.com/photo-1541167760496-1628856ab772?w=400&h=300&fit=crop
Чай чёрный,4,Чёрный чай с сахаром или без,120,https://images.unsplash.com/photo-1556679343-c7306c1976bc?w=400&h=300&fit=crop
Чикен-бургер,2,Куриная котлета, салат, соус,300,https://images.unsplash.com/photo-1606755962770-8d0a359e4500?w=400&h=300&fit=crop
Картофель фри,2,Хрустящая картошка с солью,160,https://images.unsplash.com/photo-1573080491-2e4d4e8b6a74?w=400&h=300&fit=crop
Кола,4,Освежающий напиток 0.5л,140,https://images.unsplash.com/photo-1625772299859-1d4f6b6e4f35?w=400&h=300&fit=crop
Тирамису,5,Итальянский десерт с маскарпоне,290,https://images.unsplash.com/photo-1571877227200-a0d98ea607e9?w=400&h=300&fit=crop
Чизкейк,5,Классический чизкейк с ягодным соусом,310,https://images.unsplash.com/photo-1533134242443-d4fd215305ad?w=400&h=300&fit=crop
Кетчуп,6,Томатный соус,40,https://images.unsplash.com/photo-1557180527-691a7a3150d9?w=400&h=300&fit=crop
Сырный соус,6,Нежный сырный соус,60,https://images.unsplash.com/photo-1486297678162-eb2a1910a244?w=400&h=300&fit=crop
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
