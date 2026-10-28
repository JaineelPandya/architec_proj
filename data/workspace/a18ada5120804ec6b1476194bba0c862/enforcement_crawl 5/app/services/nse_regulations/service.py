"""
app/services/nse_regulations/service.py
=========================================
Scraper for the NSE "Exchange & Market Regulations, Rules & Byelaws" page at
https://www.nseindia.com/static/regulations/exchange-market-regulations-rules-byelaws-nseil

Verified live behaviour (from direct HTML inspection):
  - Plain static HTML page (Drupal-rendered) — no backing JSON API, no
    session/cookie requirements observed; a single GET with standard browser
    headers returns the full page.
  - Documents for the four entities (NSE, NSE Clearing, NSE International
    Exchange Ltd, NSE International Clearing Ltd) live in per-entity tab
    panes. Each entity's tab-pane `id` is only discoverable at request time
    via its nav link: `<a id="staticComponent-<Entity Name>" href="#<pane_id>">`
    — the pane ids themselves (e.g. `tab-r44bjtc5ldg-0`) are regenerated per
    page render, so entities are resolved by walking these nav links rather
    than hardcoding pane ids.
  - CAUTION: naively selecting all `.tab-pane` elements double-counts —  the
    four entity panes are themselves wrapped in an outer element that also
    carries the `tab-pane` class, and the page reuses the same tab-pane/
    tab-content component markup for unrelated sections elsewhere (circulars,
    member info, market reports). Only descending into panes resolved via the
    staticComponent nav links above avoids both problems.
  - Within an entity's pane, documents are `<p><a href=".../*.pdf|*.zip">
    Title (.pdf)</a> (SIZE)</p>` paragraphs, optionally grouped under `<h6>`
    section headings (e.g. "Capital Market Segment") that persist across
    subsequent paragraphs until the next heading.
  - KNOWN GAP: NSE Clearing's document list is currently HTML-commented out
    on the live page in favour of a link to nseclearing.in — BeautifulSoup
    does not traverse comment nodes, so this entity correctly yields zero
    records rather than stale/broken links.
"""

import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.core.cache import TTLCache
from app.core.http_client import fetch_with_proxy, COMMON_HEADERS

log = logging.getLogger(__name__)

BASE_URL = "https://www.nseindia.com"
PAGE_URL = f"{BASE_URL}/static/regulations/exchange-market-regulations-rules-byelaws-nseil"

REGULATIONS_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": f"{BASE_URL}/",
}

SUPPORTED_EXTENSIONS = {"pdf", "zip"}

# Reference data only (surfaced via GET /info) — captured live from the page's
# own staticComponent nav tabs. Not enforced as an allowlist on requests;
# resolved dynamically from the page at fetch time.
ENTITIES: list[str] = [
    "NSE",
    "NSE Clearing",
    "NSE International Exchange Ltd",
    "NSE International Clearing Ltd",
]

TTL_S = 6 * 3600  # 6 hours — reference/regulatory documents change infrequently

# Module-level singleton: shared across all calls within the process.
_cache: TTLCache = TTLCache(default_ttl_seconds=TTL_S)

_SIZE_RE = re.compile(r"\(([\d.]+\s*(?:KB|MB|GB))\)", re.I)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_size(paragraph_text: str) -> str:
    """Extract the trailing '(702 KB)' / '(1.17 MB)' size annotation, if present."""
    match = _SIZE_RE.search(paragraph_text)
    return match.group(1).strip() if match else ""


def _resolve_entity_panes(soup: BeautifulSoup) -> dict[str, "BeautifulSoup"]:
    """
    Map entity name -> its tab-pane element, via the staticComponent nav links.

    Resolving through these nav links (rather than selecting `.tab-pane`
    broadly) avoids both the outer wrapper double-count and unrelated
    tab-pane sections elsewhere on the page — see module docstring.
    """
    panes: dict[str, "BeautifulSoup"] = {}
    for link in soup.select('a[id^="staticComponent-"]'):
        name = link.get_text(strip=True)
        href = link.get("href", "").strip()
        if not name or not href.startswith("#"):
            continue
        pane = soup.find(id=href[1:])
        if pane is not None:
            panes[name] = pane
    return panes


def _parse_pane_documents(entity: str, pane: "BeautifulSoup") -> list[dict]:
    """Walk one entity's tab-pane, extracting pdf/zip documents grouped by <h6> segment."""
    records: list[dict] = []
    segment = ""

    for el in pane.find_all(["h6", "p"]):
        if el.name == "h6":
            segment = el.get_text(strip=True)
            continue

        anchor = el.find("a", href=True)
        if anchor is None:
            continue

        href = anchor["href"].strip()
        file_type = href.rsplit(".", 1)[-1].split("?")[0].lower()
        if file_type not in SUPPORTED_EXTENSIONS:
            continue

        records.append({
            "entity": entity,
            "segment": segment,
            "title": anchor.get_text(strip=True),
            "circular_no": None,
            "file_type": file_type,
            "size": _parse_size(el.get_text(" ", strip=True)),
            "download_url": href,
            "type": "NSE Regulation",
        })

    return records


def _parse_documents(html: str) -> list[dict]:
    """Parse the full regulations page into normalised document records."""
    soup = BeautifulSoup(html, "html.parser")
    panes = _resolve_entity_panes(soup)

    if not panes:
        log.warning("[NSE-REG] could not resolve any entity tab-panes from the page")

    records: list[dict] = []
    for entity, pane in panes.items():
        records.extend(_parse_pane_documents(entity, pane))

    log.info("[NSE-REG] payload processed entities=%s total_documents=%s", len(panes), len(records))
    return records


# ---------------------------------------------------------------------------
# Public scraper function
# ---------------------------------------------------------------------------

async def fetch_nse_regulations(req) -> dict:
    """
    Fetch the NSE Exchange & Market Regulations, Rules & Byelaws document list.

    Results are cached in-process for TTL_S (this is reference/regulatory
    content that changes infrequently, unlike date-ranged circular feeds).
    """
    entity_filter = (getattr(req, "entity", "") or "").strip()
    via_proxy = getattr(req, "via_proxy", False)

    if entity_filter and not any(e.lower() == entity_filter.lower() for e in ENTITIES):
        raise ValueError(f"Unknown entity '{entity_filter}'. Valid: {ENTITIES}")

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    cache_key = TTLCache.make_key("nse_regulations", entity_filter.lower())
    cached = _cache.get(cache_key)
    if cached is not None:
        log.info(f"[NSE-REG] cache hit: entity={entity_filter!r} count={cached['count']}")
        return cached

    log.info(f"[NSE-REG] cache miss: entity={entity_filter!r} — fetching from upstream")

    # ------------------------------------------------------------------
    # Upstream fetch
    # ------------------------------------------------------------------
    log.info(f"[NSE-REG] documents fetch: entity={entity_filter!r} via_proxy={via_proxy} url={PAGE_URL}")

    async with httpx.AsyncClient() as client:
        resp = await fetch_with_proxy(
            client,
            PAGE_URL,
            method="GET",
            headers=REGULATIONS_HEADERS,
            timeout=30,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        html = resp.text

    all_records = _parse_documents(html)
    records = (
        [r for r in all_records if r["entity"].lower() == entity_filter.lower()]
        if entity_filter
        else all_records
    )

    log.info(f"[NSE-REG] documents fetch complete: entity={entity_filter!r} count={len(records)}")

    result = {
        "source": "nse_regulations",
        "url": PAGE_URL,
        "filters_applied": {"entity": entity_filter},
        "count": len(records),
        "records": records,
    }

    # ------------------------------------------------------------------
    # Cache store
    # ------------------------------------------------------------------
    _cache.set(cache_key, result, ttl_seconds=TTL_S)
    log.info(f"[NSE-REG] cached result: entity={entity_filter!r} ttl={TTL_S}s")

    return result
