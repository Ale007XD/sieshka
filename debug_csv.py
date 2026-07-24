import asyncio
import logging
import sys

from app.db.session import async_session_factory

from app.services.menu_import_service import MenuImportService

logging.basicConfig(level=logging.DEBUG)


async def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test.csv"
    with open(csv_path, "rb") as f:
        file_bytes = f.read()

    service = MenuImportService(session_factory=async_session_factory)
    # First: parse only to see category issues
    async with async_session_factory() as session:
        from app.services.menu_import_service import _load_categories

        cats = await _load_categories(session)
        print(f"Known categories in DB: {[c.name for c in cats]}")

    # Then: full import to see the 500 error
    print("Running full import...")
    try:
        report = await service.import_csv(file_bytes)
        print(
            f"OK imported={report.imported} skipped={len(report.skipped)} "
            f"status={report.final_status}"
        )
        for s in report.skipped:
            print(f"  SKIP row={s.row_num} name={s.name_if_present} reason={s.reason}")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
