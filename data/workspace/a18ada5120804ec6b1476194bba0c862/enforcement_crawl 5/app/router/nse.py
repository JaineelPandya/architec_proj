"""
app/router/nse.py
==================
FastAPI routes for the NSE (National Stock Exchange) scraper.

Endpoints
---------
  GET  /health        – liveness check
  GET  /info          – data source info, field reference, and usage notes
  POST /circulars     – fetch circulars for a date range (pdf + zip only)
  POST /announcement  – fetch NSE corporate announcements
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.nse.schema import NseCircularsRequest
from app.services.nse.service import (
    API_URL,
    SUPPORTED_EXTENSIONS,
    fetch_nse_circulars,
)
from app.services.nse.announcements.schema import (
    AnnouncementQuery,
    AnnouncementResponse,
)
from app.services.nse.announcements.service import fetch_nse_announcements

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    """Module liveness check."""
    log.debug("[NSE] health check called")
    return {"status": "ok", "module": "nse"}


@router.get("/info")
def info():
    """Return data source info, valid filter values, and field reference."""
    log.debug("[NSE] info called")
    return {
        "source": "nse",
        "source_url": API_URL,
        "description": (
            "NSE circulars — fetches from the NSE circulars JSON API and "
            "returns only items with supported file extensions (pdf, zip)."
        ),
        "fields": [
            "source",
            "circular_id",
            "full_reference",
            "department",
            "title",
            "issue_date",
            "applicable_to_nse",
            "url",
            "download_url",
            "source_item_key",
        ],
        "supported_file_extensions": sorted(SUPPORTED_EXTENSIONS),
        "notes": {
            "from_date / to_date": (
                "Required, inclusive. Formats accepted: DD-MM-YYYY or YYYY-MM-DD."
            ),
            "headers": (
                "NSE blocks requests without User-Agent, Accept, and Referer headers; "
                "these are injected automatically by the service."
            ),
            "circular_id": (
                "Built from fileDept + circNumber, normalised (strip '/', uppercase). "
                "Used as the primary identifier."
            ),
            "source_item_key": (
                "circular_id:circDepartment — allows the same circular_id from "
                "different departments to remain distinct."
            ),
            "download_url / url": (
                "Taken from circFilelink when present; falls back to "
                "https://nsearchives.nseindia.com/content/circulars/{circular_id}.pdf"
            ),
            "via_proxy": (
                "Defaults to false. NSE may reject requests routed through "
                "proxy aggregators that alter or strip required headers."
            ),
        },
    }


@router.post("/circulars")
async def circulars(request: NseCircularsRequest):
    """
    Fetch NSE circulars for a date range from the NSE circulars API.

    - **from_date** / **to_date**: DD-MM-YYYY or YYYY-MM-DD, both required (inclusive)

    Only circulars with 'pdf' or 'zip' attachments are returned; other file
    types are silently skipped.

    Note: NSE requires specific request headers (User-Agent, Accept, Referer);
    these are injected automatically — no client-side configuration is needed.
    """
    log.info(
        f"[NSE] circulars request: from_date={request.from_date!r} "
        f"to_date={request.to_date!r} via_proxy={request.via_proxy}"
    )
    try:
        result = await fetch_nse_circulars(request)

        log.info(
            f"[NSE] circulars complete: from_date={request.from_date!r} "
            f"to_date={request.to_date!r} count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[NSE] validation error on circulars: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[NSE] upstream error on circulars: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[NSE] unexpected error on circulars")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch NSE circulars. See server logs.",
        )


@router.post("/announcement", response_model=AnnouncementResponse)
async def announcement(query: AnnouncementQuery):
    """
    Fetch NSE corporate announcements.

    - **index**: Market segment (``equities``, ``sme``, ``debt``, ``mf``,
      ``invitsreits``). Defaults to ``equities``.
    - **symbol**: NSE trading symbol (optional). Leave empty for all companies.
    - **from_date** / **to_date**: DD-MM-YYYY or YYYY-MM-DD dates (optional). Leave empty for
      the exchange's default window.
    - **fno**: Set ``true`` to restrict results to F&O stocks.

    NSE requires a live browser-like session (cookie warm-up) before the
    corporate announcements API will respond; this is handled automatically.
    """
    log.info(
        "[NSE] announcement request: index=%r symbol=%r "
        "from_date=%r to_date=%r fno=%s via_proxy=%s",
        query.index,
        query.symbol,
        query.from_date,
        query.to_date,
        query.fno,
        query.via_proxy,
    )
    try:
        result = await fetch_nse_announcements(query)
        log.info(
            "[NSE] announcement complete: index=%r count=%d",
            query.index,
            result.count,
        )
        return result

    except ValueError as exc:
        log.warning("[NSE] validation error on announcement: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        log.exception("[NSE] unexpected error on announcement")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch NSE announcements. See server logs.",
        )
