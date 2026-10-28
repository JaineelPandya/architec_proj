"""
app/services/bse/service.py
=============================
Scraper for BSE (Bombay Stock Exchange) Notices & Circulars at
https://www.bseindia.com/markets/marketinfo/noticescirculars — via the
Angular SPA's own backing API (verified by inspecting the site's compiled JS
bundle, `NoticesCircularsComponent.fetchNoticesData` / `imgbtnPdf_Click`):

  - GET https://api.bseindia.com/BseIndiaAPI/api/getDataAdvance_New/w
        ?strTxtNoticeNo=&strTxtDate=<YYYY-MM-DD>&strTxtTodate=<YYYY-MM-DD>
        &strScripcode=&strDep=<department>&strSegment=<segment>
        &subject=<subject>&category=<category>&containgtext=
                            – returns JSON {"Table": [...]}; each row has
                              Notice_no, Notice_Date, Subject, Segment_Name,
                              category_name, Dept_Name, Attach_Flag.
  - The API blocks requests that don't carry matching Referer/Origin headers
    (returns an HTTP redirect instead of JSON) — these are injected here,
    mirroring the NSE service's header handling.
  - No upstream pagination was observed — a single request returns the full
    matching set for the given date range (verified: a 6-month window
    returned 2500+ rows in one response).
  - Each row's `FileName` field is BSE's own already-resolved, unauthenticated
    direct PDF URL (e.g. https://www.bseindia.com/downloads/UploadDocs/Notices/
    <Notice_No>/<Notice_No>.pdf) — used as-is for download_url. Falls back to
    constructing that same URL pattern from Notice_No only in the rare case
    FileName is missing.
  - NOTE: BSE migrated this listing endpoint from `getDataAdvance/w` to
    `getDataAdvance_New/w` — the old path still returns HTTP 200 with valid
    Referer/Origin headers, but silently responds with an empty
    `{"Table": []}` instead of a 404, which previously looked like an
    upstream indexing lag. The `_New` suffix is the current live endpoint
    (verified against BSE's site for same-day notices).
"""

import logging
from datetime import date, datetime
from urllib.parse import urlencode

import httpx

from app.core.cache import TTLCache
from app.core.http_client import fetch_with_proxy, COMMON_HEADERS

log = logging.getLogger(__name__)

API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
LISTING_URL = f"{API_BASE}/getDataAdvance_New/w"
DIRECT_PDF_BASE = "https://www.bseindia.com/downloads/UploadDocs/Notices"
NOTICES_PAGE_URL = "https://www.bseindia.com/markets/marketinfo/noticescirculars?id=0&txtscripcd=&pagecont=&subject="

# BSE rejects requests that omit these headers (silently redirects instead of
# returning JSON).
BSE_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": NOTICES_PAGE_URL,
    "Origin": "https://www.bseindia.com",
}

TTL_SHORT_S = 10 * 60        # 10 minutes — end_date is today or future
TTL_LONG_S = 7 * 24 * 3600   # 7 days    — end_date is fully in the past

# Module-level singleton: shared across all calls within the process.
_cache: TTLCache = TTLCache(default_ttl_seconds=TTL_SHORT_S)

# Reference data only (surfaced via GET /bse/info) — captured live from BSE's
# own /segment/w?flag=S|C|D dropdown-data endpoints. Not enforced on
# requests: BSE may add segments/categories/departments over time and these
# are passed through to the upstream API as-is.
SEGMENTS: list[str] = [
    "BSEHITECH", "Commodity Derivatives", "Currency Derivatives", "Debt",
    "Derivatives", "EGR", "Equity", "General", "IA/RA",
    "Investment Advisers", "IRD", "Mutual Fund", "NDS", "Research Analysts",
    "SLB", "SME", "SSE", "Startups",
]

CATEGORIES: list[str] = [
    "Circulars Listed Companies", "Company related", "Compliance",
    "Corporate Actions", "Index", "Others", "Settlement/RMS", "Tenders",
    "Trading",
]

DEPARTMENTS: list[str] = [
    "Bad Delivery Cell", "BSE Training Institute", "BTI",
    "Business Development", "Business Operations", "Clearing & Settlement",
    "Corporate Communications", "Corporate Relations",
    "Corporate Secreterial", "DAPM - General Administration",
    "Datawarehouse Regulatory", "Debt", "Derivatives", "DIS", "DOSS",
    "Economist Cell", "ED's Office", "Estate & Maintenance",
    "Exchange Compliance", "Finance & Accounts", "Fixed Income",
    "General Administration", "Helpdesk", "HR", "Human Resources", "ICC",
    "Index Cell", "Information Systems", "Information Systems (Projects)",
    "Information Technology", "Inspection", "Investors Services Cell",
    "IPF Secretariat", "ISMS", "IT-Networks", "IT-Operations", "Legal",
    "Listing Business Relationship", "Listing Compliance",
    "Listing Compliance-Monitoring", "Listing Operations",
    "Market Operations", "Marketing", "MD office",
    "Member Institution Sales", "Membership", "Membership Compliance",
    "Membership Services", "Personnel", "Post Trade",
    "Premises Management", "Regulatory Correspondence Dept.",
    "S&P BSE Index", "Secretarial", "Security", "SME", "Surveillance",
    "Tainted Shares", "Trading Development", "Trading Operations",
    "Unified Business Commercial",
]


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


def _build_listing_url(
    start_date: date, end_date: date, department: str, segment: str, category: str, subject: str
) -> str:
    """Build the BSE getDataAdvance API URL with properly formatted query params."""
    query = urlencode({
        "strTxtNoticeNo": "",
        "strTxtDate": start_date.strftime("%Y-%m-%d"),
        "strTxtTodate": end_date.strftime("%Y-%m-%d"),
        "strScripcode": "",
        "strDep": department,
        "strSegment": segment,
        "subject": subject,
        "category": category,
        "containgtext": "",
    })
    return f"{LISTING_URL}?{query}"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_notice_date(raw_value: str) -> str:
    """
    Parse Notice_Date from the BSE API (ISO datetime string) and return an
    ISO-8601 date string.

    Returns the raw value unchanged if parsing fails so callers always get a string.
    """
    try:
        return datetime.fromisoformat(str(raw_value).strip()).date().isoformat()
    except ValueError:
        log.warning("[BSE] Could not parse Notice_Date value=%r; returning raw", raw_value)
        return str(raw_value).strip()


def _direct_download_url(notice_no: str) -> str:
    """Return BSE's direct, unauthenticated PDF URL for a given Notice_No."""
    return f"{DIRECT_PDF_BASE}/{notice_no}/{notice_no}.pdf"


def _parse_response(payload: dict) -> list[dict]:
    """Parse the raw JSON payload from the BSE getDataAdvance_New API."""
    records: list[dict] = []

    for item in payload.get("Table", []):
        notice_no = str(item.get("Notice_No", "")).strip()
        file_name = str(item.get("FileName") or "").strip()

        records.append({
            "circular_no": notice_no,
            "subject": str(item.get("Subject", "")).strip(),
            "segment": str(item.get("Segment_Name", "")).strip(),
            "category": str(item.get("category_name", "")).strip(),
            "department": str(item.get("Dept_Name", "")).strip(),
            "download_url": file_name or (_direct_download_url(notice_no) if notice_no else ""),
            "date": _parse_notice_date(item.get("Notice_Date", "")),
            "type": "BSE Circular",
        })

    log.info("[BSE] payload processed total_items=%s", len(records))
    return records


# ---------------------------------------------------------------------------
# Public scraper function
# ---------------------------------------------------------------------------

async def fetch_bse_circulars(req) -> dict:
    """
    Fetch BSE Notices & Circulars for a date range from the getDataAdvance API.

    Results are cached in-process:
      - end_date >= today → 10-minute TTL (BSE may publish new notices later
                            the same day)
      - end_date < today  → 7-day TTL (past listings are immutable)
    """
    start_date = _parse_input_date(req.start_date)
    end_date = _parse_input_date(req.end_date)
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    segment = (req.segment or "").strip()
    category = (req.category or "").strip()
    department = (req.department or "").strip()
    subject = (getattr(req, "subject", "") or "").strip()
    via_proxy = getattr(req, "via_proxy", False)

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    cache_key = TTLCache.make_key(
        "bse_circulars", start_date.isoformat(), end_date.isoformat(),
        segment, category, department, subject,
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        log.info(
            f"[BSE] cache hit: start_date={start_date} end_date={end_date} "
            f"count={cached['count']}"
        )
        return cached

    log.info(
        f"[BSE] cache miss: start_date={start_date} end_date={end_date} — fetching from upstream"
    )

    # ------------------------------------------------------------------
    # Upstream fetch
    # ------------------------------------------------------------------
    url = _build_listing_url(start_date, end_date, department, segment, category, subject)

    log.info(
        f"[BSE] circulars fetch: start_date={start_date} end_date={end_date} "
        f"via_proxy={via_proxy} url={url}"
    )

    async with httpx.AsyncClient() as client:
        resp = await fetch_with_proxy(
            client,
            url,
            method="GET",
            headers=BSE_HEADERS,
            timeout=30,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        payload = resp.json()

        records = _parse_response(payload)

    log.info(
        f"[BSE] circulars fetch complete: start_date={start_date} end_date={end_date} "
        f"count={len(records)}"
    )

    result = {
        "source": "bse",
        "url": url,
        "filters_applied": {
            "start_date": start_date.strftime("%d-%m-%Y"),
            "end_date": end_date.strftime("%d-%m-%Y"),
            "segment": segment,
            "category": category,
            "department": department,
            "subject": subject,
        },
        "count": len(records),
        "records": records,
    }

    # ------------------------------------------------------------------
    # Cache store — TTL depends on whether the range is fully in the past
    # ------------------------------------------------------------------
    today = date.today()
    ttl = TTL_SHORT_S if end_date >= today else TTL_LONG_S
    _cache.set(cache_key, result, ttl_seconds=ttl)
    log.info(
        f"[BSE] cached result: start_date={start_date} end_date={end_date} "
        f"ttl={ttl}s ({'short' if end_date >= today else 'long'})"
    )

    return result
