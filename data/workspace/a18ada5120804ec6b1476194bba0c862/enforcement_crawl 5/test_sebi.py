import asyncio
from app.core.http_client import COMMON_HEADERS
import httpx
from bs4 import BeautifulSoup

async def main():
    landing_url = "https://www.sebi.gov.in/legal/circulars/may-2008/reporting-of-information-on-a-quarterly-basis-registrars-to-issue-and-share-transfer-agents_6935.html"
    async with httpx.AsyncClient() as client:
        resp = await client.get(landing_url, headers={"User-Agent": COMMON_HEADERS["User-Agent"]})
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # print all text to find where "Cir. No. MIRSD" is
        for p in soup.find_all(["p", "td", "div", "span"]):
            text = p.get_text(strip=True)
            if "MIRSD" in text or "Cir" in text:
                print(f"[{p.name}] {text}")

asyncio.run(main())
