import asyncio
from app.services.sebi.schema import SebiCircularRequest
from app.services.sebi.service import scrape_sebi_circulars

async def main():
    req = SebiCircularRequest(from_date="01-01-2024", to_date="31-01-2024")
    try:
        res = await scrape_sebi_circulars(req)
        for r in res["records"][:10]:
            print(f"{r.get('circular_no')} -> {r.get('department')} ({r.get('abbreviation')})")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
