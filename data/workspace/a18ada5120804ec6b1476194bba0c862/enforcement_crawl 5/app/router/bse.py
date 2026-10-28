"""
app/router/bse.py
===================
FastAPI routes for the BSE (Bombay Stock Exchange) Notices & Circulars scraper.

Endpoints
---------
  GET  /health     – liveness check
  GET  /info       – valid filter values and field reference
  POST /circular   – scrape BSE circulars for a date range
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.bse.schema import BseCircularRequest
from app.services.bse.service import (
    CATEGORIES,
    DEPARTMENTS,
    NOTICES_PAGE_URL,
    SEGMENTS,
    fetch_bse_circulars,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    """Module liveness check."""
    log.debug("[BSE] health check called")
    return {"status": "ok", "module": "bse"}


@router.get("/info")
def info():
    """Return data source info and valid filter values."""
    log.debug("[BSE] info called")
    return {
        "source": "bse",
        "source_url": NOTICES_PAGE_URL,
        "description": (
            "BSE Notices & Circulars — fetches from BSE's own listing API "
            "(getDataAdvance_New) behind the 'Notices & Circulars' page."
        ),
        "fields": [
            "notice_number",
            "subject",
            "segment",
            "category",
            "department",
            "download_url",
            "date",
            "type",
        ],
        "valid_segments": SEGMENTS,
        "valid_categories": CATEGORIES,
        "valid_departments": DEPARTMENTS,
        "notes": {
            "start_date / end_date": (
                "Required, inclusive. Formats accepted: DD-MM-YYYY or YYYY-MM-DD."
            ),
            "segment / category / department": (
                "Required. Must match one of the values listed above exactly "
                "(as published by BSE's own dropdown data) — not validated "
                "server-side, passed through as-is to the upstream API."
            ),
            "headers": (
                "BSE's API blocks requests without matching Referer/Origin "
                "headers (returns a redirect instead of JSON); these are "
                "injected automatically by the service."
            ),
            "download_url": (
                "Taken directly from the upstream API's own FileName field — "
                "BSE's already-resolved, unauthenticated direct PDF URL "
                "(bseindia.com/downloads/UploadDocs/Notices/<Notice_No>/"
                "<Notice_No>.pdf). Only constructed locally from Notice_No "
                "in the rare case FileName is missing from a record."
            ),
            "via_proxy": (
                "Defaults to false. BSE may reject requests routed through "
                "proxy aggregators that alter or strip required headers."
            ),
        },
    }


@router.post("/circular")
async def circular(request: BseCircularRequest):
    """
    Scrape BSE Notices & Circulars for a date range.

    - **start_date** / **end_date**: DD-MM-YYYY or YYYY-MM-DD, both required (inclusive)
    - **segment**, **category**, **department**: required filters
    - **subject**: optional free-text filter
    """
    log.info(
        f"[BSE] circular request: start_date={request.start_date!r} "
        f"end_date={request.end_date!r} segment={request.segment!r} "
        f"category={request.category!r} department={request.department!r} "
        f"via_proxy={request.via_proxy}"
    )
    try:
        result = await fetch_bse_circulars(request)

        log.info(
            f"[BSE] circular complete: start_date={request.start_date!r} "
            f"end_date={request.end_date!r} count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[BSE] validation error on circular: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[BSE] upstream error on circular: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[BSE] unexpected error on circular")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch BSE circulars. See server logs.",
        )
