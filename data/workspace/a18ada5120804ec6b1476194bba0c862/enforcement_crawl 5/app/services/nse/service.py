"""
app/services/nse/service.py
============================
Scraper for NSE (National Stock Exchange) circulars at
https://www.nseindia.com/api/circulars — date-range query.

Verified live behaviour (from direct API inspection):
  - GET  /api/circulars?fromDate=DD-MM-YYYY&toDate=DD-MM-YYYY
                         – returns JSON: {"data": [...], ...}
                         – each item contains fileExt, fileDept, circNumber,
                           cirDate, circDisplayNo, circFilelink, sub,
                           circDepartment fields.
                         – NSE blocks requests without User-Agent, Accept,
                           and Referer headers (returns 401/403).
  - Only items where fileExt is "pdf" or "zip" are kept; others are skipped.
  - circular_id is built from fileDept + circNumber, normalised (strip "/", uppercase).
  - source_item_key is "circular_id:circDepartment" to allow the same circular_id
    from different departments to remain distinct.
  - issue_date is parsed from cirDate in %Y%m%d format.
  - download_url / url come from circFilelink, falling back to the archive URL
    https://nsearchives.nseindia.com/content/circulars/{circular_id}.pdf
  - Retry and timeout are delegated entirely to core/http_client.fetch_with_proxy;
    no retry logic is reimplemented here.
"""

import logging
from datetime import date, datetime
from urllib.parse import urlencode

import httpx

from app.core.cache import TTLCache
from app.core.http_client import fetch_with_proxy, COMMON_HEADERS

log = logging.getLogger(__name__)

API_URL = "https://www.nseindia.com/api/circulars"
BASE_URL = "https://www.nseindia.com"
ARCHIVE_URL = "https://nsearchives.nseindia.com/content/circulars"

# NSE rejects requests that omit these headers.
NSE_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/",
}

SUPPORTED_EXTENSIONS = {"pdf", "zip"}

TTL_SHORT_S = 10 * 60       # 10 minutes — to_date is today or future
TTL_LONG_S  = 7 * 24 * 3600  # 7 days    — to_date is fully in the past

# Module-level singleton: shared across all calls within the process.
_cache: TTLCache = TTLCache(default_ttl_seconds=TTL_SHORT_S)


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------

def _parse_input_date(value: str) -> date:
    """Accept DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YYYY."""
    if not value or not value.strip():
        raise ValueError("Date value is required")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date '{value}'. Use DD-MM-YYYY or YYYY-MM-DD.")


def _build_listing_url(from_date: date, to_date: date) -> str:
    """Build the NSE circulars API URL with properly formatted date query params."""
    query = urlencode(
        {
            "fromDate": from_date.strftime("%d-%m-%Y"),
            "toDate": to_date.strftime("%d-%m-%Y"),
        }
    )
    return f"{API_URL}?{query}"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_circular_id(raw_id: str) -> str:
    """Normalise a raw circular ID: strip '/', strip whitespace, uppercase."""
    return raw_id.replace("/", "").strip().upper()


def _get_archive_url(circular_id: str) -> str:
    """Return the canonical NSE archive download URL for a given circular_id."""
    return f"{ARCHIVE_URL}/{circular_id}.pdf"


def _parse_issue_date(raw_value: str) -> str:
    """
    Parse cirDate from the NSE API (%Y%m%d) and return an ISO-8601 date string.

    Returns the raw value unchanged if parsing fails so callers always get a string.
    """
    try:
        return datetime.strptime(str(raw_value).strip(), "%Y%m%d").date().isoformat()
    except ValueError:
        log.warning("[NSE] Could not parse cirDate value=%r; returning raw", raw_value)
        return str(raw_value).strip()


def _parse_response(payload: dict) -> list[dict]:
    """
    Parse the raw JSON payload from the NSE circulars API.

    Keeps only items where fileExt is 'pdf' or 'zip'.  Skips everything else
    and logs a summary count at the end.
    """
    circulars: list[dict] = []
    skipped_unsupported = 0

    for item in payload.get("data", []):
        file_ext = str(item.get("fileExt", "")).strip().lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            skipped_unsupported += 1
            continue

        file_dept = str(item.get("fileDept", "")).strip().upper()
        circ_number = str(item.get("circNumber", "")).strip()
        circular_id = _parse_circular_id(f"{file_dept}{circ_number}")

        issue_date = _parse_issue_date(item.get("cirDate", ""))
        download_url = (
            str(item.get("circFilelink", "")).strip()
            or _get_archive_url(circular_id)
        )

        circ_dept = str(item.get("circDepartment", "")).strip()
        circular_no = str(item.get("circDisplayNo", "")).strip() or None

        circulars.append(
            {
                "source": "nse",
                "circular_id": circular_id,
                "circular_no": circular_no,
                "department": circ_dept,
                "title": str(item.get("sub", "")).strip(),
                "issue_date": issue_date,
                "download_url": download_url,
            }
        )

    log.info(
        "[NSE] payload processed total_items=%s supported_circulars=%s skipped_unsupported=%s",
        len(payload.get("data", [])),
        len(circulars),
        skipped_unsupported,
    )
    return circulars


# ---------------------------------------------------------------------------
# Public scraper function
# ---------------------------------------------------------------------------

async def fetch_nse_circulars(req) -> dict:
    """
    Fetch NSE circulars for a date range from the NSE circulars API.

    Only 'pdf' and 'zip' attachments are included in the response; other file
    types are silently skipped.  Retry and timeout handling are delegated to
    core/http_client.fetch_with_proxy.

    Results are cached in-process:
      - to_date >= today  → 10-minute TTL (NSE may publish new circulars later
                            the same day)
      - to_date < today   → 7-day TTL (past listings are immutable)
    """
    start_date = _parse_input_date(req.from_date)
    end_date = _parse_input_date(req.to_date)
    if start_date > end_date:
        raise ValueError("from_date must not be after to_date")

    via_proxy = getattr(req, "via_proxy", False)

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    cache_key = TTLCache.make_key(
        "nse_circulars", start_date.isoformat(), end_date.isoformat()
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        log.info(
            f"[NSE] cache hit: from_date={start_date} to_date={end_date} "
            f"count={cached['count']}"
        )
        return cached

    log.info(
        f"[NSE] cache miss: from_date={start_date} to_date={end_date} — fetching from upstream"
    )

    # ------------------------------------------------------------------
    # Upstream fetch
    # ------------------------------------------------------------------
    url = _build_listing_url(start_date, end_date)

    log.info(
        f"[NSE] circulars fetch: from_date={start_date} to_date={end_date} "
        f"via_proxy={via_proxy} url={url}"
    )

    async with httpx.AsyncClient() as client:
        resp = await fetch_with_proxy(
            client,
            url,
            method="GET",
            headers=NSE_HEADERS,
            timeout=30,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        payload = resp.json()

    circulars = _parse_response(payload)

    log.info(
        f"[NSE] circulars fetch complete: from_date={start_date} to_date={end_date} "
        f"count={len(circulars)}"
    )

    result = {
        "source": "nse",
        "url": url,
        "filters_applied": {
            "from_date": start_date.strftime("%d-%m-%Y"),
            "to_date": end_date.strftime("%d-%m-%Y"),
        },
        "count": len(circulars),
        "records": circulars,
    }

    # ------------------------------------------------------------------
    # Cache store — TTL depends on whether the range is fully in the past
    # ------------------------------------------------------------------
    today = date.today()
    ttl = TTL_SHORT_S if end_date >= today else TTL_LONG_S
    _cache.set(cache_key, result, ttl_seconds=ttl)
    log.info(
        f"[NSE] cached result: from_date={start_date} to_date={end_date} "
        f"ttl={ttl}s ({'short' if end_date >= today else 'long'})"
    )

    return result
