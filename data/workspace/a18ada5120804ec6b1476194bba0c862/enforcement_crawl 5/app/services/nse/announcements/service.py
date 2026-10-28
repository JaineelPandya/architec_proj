"""
app/services/nse/announcements/service.py
==========================================
Service function for fetching NSE corporate announcements from
https://www.nseindia.com/api/corporate-announcements

Key behaviour (ported from nse_corporate_crawler and adapted to use the
enforcement project's shared infrastructure):

  - NSE requires a live browser-like session before the JSON API works.
    We replicate NSESession.connect() by visiting the corporate filings
    page first with the same httpx.AsyncClient so cookies are retained.

  - Query params: index (required), symbol, from_date, to_date, fno.

  - Dates are serialised as DD-MM-YYYY for the upstream API.

  - Response is a list of dicts (or {"data": [...]}); each item is
    mapped to an Announcement model.

  - Results are cached in-process using the shared TTLCache:
      * 10-minute TTL when to_date >= today (NSE may publish more)
      * 7-day TTL when to_date < today (past data is immutable)
"""

import logging
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.cache import TTLCache
from app.core.http_client import fetch_with_proxy
from app.services.nse.announcements.constants import (
    API_HEADERS,
    CORPORATE_ANNOUNCEMENTS_API,
    CORPORATE_ANNOUNCEMENTS_PAGE,
    PAGE_HEADERS,
)
from app.services.nse.announcements.schema import (
    Announcement,
    AnnouncementQuery,
    AnnouncementResponse,
)

log = logging.getLogger(__name__)

TTL_SHORT_S = 10 * 60        # 10 minutes — live data
TTL_LONG_S  = 7 * 24 * 3600  # 7 days    — historical data

_cache: TTLCache = TTLCache(default_ttl_seconds=TTL_SHORT_S)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_broadcast_dates(raw_value: Any) -> tuple[str | None, str | None]:
    """
    Parse an_dt from NSE API (e.g. '07-Oct-2026 16:12:54') and return
    (broadcast_date, broadcast_datetime) formatted in DD-MM-YYYY standard.
    """
    if not raw_value:
        return None, None
    val = str(raw_value).strip()
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %I:%M:%S %p",
        "%d-%b-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
    ):
        try:
            dt = datetime.strptime(val, fmt)
            date_str = dt.strftime("%d-%m-%Y")
            datetime_str = (
                dt.strftime("%d-%m-%Y %H:%M:%S")
                if ("%H" in fmt or "%I" in fmt)
                else date_str
            )
            return date_str, datetime_str
        except ValueError:
            continue
    return val, val


def _build_params(query: AnnouncementQuery) -> dict[str, str]:
    """Translate AnnouncementQuery into the NSE API query-string dict."""
    params: dict[str, str] = {"index": query.index}

    if query.symbol:
        params["symbol"] = query.symbol.upper()

    if query.from_date:
        params["from_date"] = query.from_date.strftime("%d-%m-%Y")

    if query.to_date:
        params["to_date"] = query.to_date.strftime("%d-%m-%Y")

    if query.fno:
        params["fno"] = "true"

    return params


def _build_url(params: dict[str, str]) -> str:
    return f"{CORPORATE_ANNOUNCEMENTS_API}?{urlencode(params)}"


def _parse_item(item: dict[str, Any], segment: str) -> Announcement:
    """Map a single raw API item to an Announcement model with DD-MM-YYYY dates."""
    seq_id = item.get("seq_id")
    b_date, b_datetime = _parse_broadcast_dates(item.get("an_dt"))
    return Announcement(
        announcement_id=str(seq_id) if seq_id is not None else None,
        segment=segment,
        symbol=item.get("symbol"),
        company_name=item.get("sm_name"),
        subject=item.get("desc"),
        details=item.get("attchmntText"),
        attachment_url=item.get("attchmntFile"),
        broadcast_date=b_date,
        broadcast_datetime=b_datetime,
    )


def _parse_payload(
    payload: Any,
    segment: str,
) -> list[Announcement]:
    """Extract a list of Announcement objects from the raw API payload."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data", [])
    else:
        log.warning(
            "[NSE Announcements] Unexpected payload type: %s",
            type(payload).__name__,
        )
        return []

    if not isinstance(items, list):
        log.warning("[NSE Announcements] Payload items is not a list.")
        return []

    records: list[Announcement] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            records.append(_parse_item(item, segment))
        except Exception:
            log.exception("[NSE Announcements] Failed to parse item: %s", item)

    return records


def _cache_key(query: AnnouncementQuery) -> str:
    return TTLCache.make_key(
        "nse_announcements",
        query.index,
        query.symbol or "",
        query.from_date.isoformat() if query.from_date else "",
        query.to_date.isoformat() if query.to_date else "",
        str(query.fno),
    )


def _ttl_for(to_date: date | None) -> int:
    """Return the appropriate cache TTL based on whether data is historical."""
    if to_date is None or to_date >= date.today():
        return TTL_SHORT_S
    return TTL_LONG_S


# ---------------------------------------------------------------------------
# NSE session warm-up
# ---------------------------------------------------------------------------

async def _warm_up_session(client: httpx.AsyncClient, via_proxy: bool = True) -> None:
    """
    Visit the NSE corporate filings page so NSE sets the required
    cookies/session state on the shared client before we hit the API.

    This replicates NSESession.connect() from nse_corporate_crawler.
    A failed warm-up is logged as a warning but does not raise — the
    subsequent API call may still succeed if NSE is lenient.
    """
    try:
        log.info(
            "[NSE Announcements] Session warm-up: GET %s (via_proxy=%s)",
            CORPORATE_ANNOUNCEMENTS_PAGE,
            via_proxy,
        )
        resp = await fetch_with_proxy(
            client,
            CORPORATE_ANNOUNCEMENTS_PAGE,
            headers=PAGE_HEADERS,
            timeout=30,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        log.info(
            "[NSE Announcements] Session established | status=%s | cookies=%d",
            resp.status_code,
            len(client.cookies),
        )
    except Exception as exc:
        log.warning(
            "[NSE Announcements] Session warm-up failed (continuing): %s", exc
        )


# ---------------------------------------------------------------------------
# Public service function
# ---------------------------------------------------------------------------

async def fetch_nse_announcements(
    query: AnnouncementQuery,
) -> AnnouncementResponse:
    """
    Fetch NSE corporate announcements for the given query.

    Steps:
      1. Check in-process TTL cache.
      2. Warm up NSE session (visit filings page for cookies).
      3. Call the JSON API.
      4. Parse & cache the result.

    Raises:
      httpx.HTTPStatusError: on non-2xx from NSE after all retries.
      ValueError: on invalid query.
    """
    params = _build_params(query)
    url = _build_url(params)

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    cache_key = _cache_key(query)
    cached = _cache.get(cache_key)
    if cached is not None:
        log.info(
            "[NSE Announcements] Cache hit: index=%s symbol=%s count=%d",
            query.index,
            query.symbol,
            cached.count,
        )
        return cached

    log.info(
        "[NSE Announcements] Cache miss — fetching from upstream: %s", url
    )

    # ------------------------------------------------------------------
    # Upstream fetch (with session warm-up)
    # ------------------------------------------------------------------
    via_proxy = getattr(query, "via_proxy", True)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        await _warm_up_session(client, via_proxy=via_proxy)

        log.info("[NSE Announcements] GET %s (via_proxy=%s)", url, via_proxy)
        resp = await fetch_with_proxy(
            client,
            CORPORATE_ANNOUNCEMENTS_API,
            params=params,
            headers=API_HEADERS,
            timeout=30,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        payload = resp.json()

    records = _parse_payload(payload, query.index)

    log.info(
        "[NSE Announcements] Fetch complete: index=%s symbol=%s count=%d",
        query.index,
        query.symbol,
        len(records),
    )

    result = AnnouncementResponse(
        requested_url=url,
        filters={
            "index": query.index,
            "symbol": query.symbol,
            "from_date": (
                query.from_date.isoformat() if query.from_date else None
            ),
            "to_date": (
                query.to_date.isoformat() if query.to_date else None
            ),
            "fno": query.fno,
        },
        count=len(records),
        records=records,
    )

    # ------------------------------------------------------------------
    # Cache store
    # ------------------------------------------------------------------
    ttl = _ttl_for(query.to_date)
    _cache.set(cache_key, result, ttl_seconds=ttl)
    log.info(
        "[NSE Announcements] Cached result: ttl=%ds (%s)",
        ttl,
        "short" if ttl == TTL_SHORT_S else "long",
    )

    return result
