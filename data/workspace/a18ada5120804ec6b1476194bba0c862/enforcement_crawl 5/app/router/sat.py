"""
app/router/sat.py
==================
FastAPI routes for the Securities Appellate Tribunal (SAT) orders scraper.

Endpoints
---------
  GET  /health              – liveness check
  GET  /info                – valid filter values and column reference
  POST /orders/date-wise    – scrape orders for a date range (SEBI / IRDA / PFRDA)
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.sat.schema import SatOrdersDateWiseRequest
from app.services.sat.service import (
    APL_TYPE_MAP,
    CHUNK_MONTHS,
    MAX_CHUNKS,
    ORDERS_PAGE_URL,
    scrape_sat_orders_datewise,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    """Module liveness check."""
    log.debug("[SAT] health check called")
    return {"status": "ok", "module": "sat"}


@router.get("/info")
def info():
    """Return data source info, valid filter values, and column reference."""
    log.debug("[SAT] info called")
    return {
        "source": "sat",
        "source_url": ORDERS_PAGE_URL,
        "description": (
            "Securities Appellate Tribunal case orders — 'Date Wise' search tab only "
            "(Appeal No./AL No./Party Name tabs are not implemented)."
        ),
        "fields": [
            "sl_no",
            "al_no",
            "appeal_no",
            "parties",
            "appellant",
            "respondent",
            "court",
            "order_date",
            "view_order_url",
        ],
        "valid_apl_type": sorted(APL_TYPE_MAP.keys()),
        "notes": {
            "apl_type": "Required. sebi | irda | pfrda.",
            "from_date / to_date": (
                "Required, inclusive. Formats accepted: DD-MM-YYYY or YYYY-MM-DD."
            ),
            "captcha": (
                "The site's captcha is a client-side-only UI gate (verified from live "
                "inspection of orders.js) — the answer is never sent to the server, so "
                "no OCR/captcha-solving is performed or required."
            ),
            "session": (
                "Requires a stateful cookie session and a security_token that rotates "
                "on every request. When via_proxy is enabled, SAT requests share one "
                "ScrapeOps sticky session and explicitly forward the upstream cookie."
            ),
            "pagination": (
                f"No upstream pagination — ranges are auto-split into <= {CHUNK_MONTHS}-month "
                f"chunks and merged, capped at {MAX_CHUNKS} chunks as a safety limit."
            ),
            "view_order_url": (
                "Appears session/token-bound on the upstream site — treat as reference "
                "metadata rather than a stable, independently-fetchable URL."
            ),
        },
    }


@router.post("/orders/date-wise")
async def orders_date_wise(request: SatOrdersDateWiseRequest):
    """
    Scrape SAT orders for a date range from the "Date Wise" search tab.

    - **apl_type**: sebi | irda | pfrda (optional, defaults to 'sebi')
    - **from_date** / **to_date**: DD-MM-YYYY or YYYY-MM-DD, both required (inclusive)

    Wide ranges are automatically split into <=3-month chunks against the
    upstream site and merged; capped at 40 chunks (~10 years) as a safety limit.

    Note: the site's captcha is a client-side-only UI gate (verified from live
    inspection of orders.js) — it is never validated server-side, so no captcha
    solving is performed or required.
    """
    log.info(
        f"[SAT] orders/date-wise request: apl_type={request.apl_type!r} "
        f"from_date={request.from_date!r} to_date={request.to_date!r} "
        f"via_proxy={request.via_proxy}"
    )
    try:
        if request.apl_type.strip().lower() not in APL_TYPE_MAP:
            raise ValueError(
                f"Unknown apl_type '{request.apl_type}'. Valid: {sorted(APL_TYPE_MAP)}"
            )

        result = await scrape_sat_orders_datewise(request)

        log.info(
            f"[SAT] orders/date-wise complete: apl_type={request.apl_type!r} "
            f"chunks={result.get('chunks_fetched')} count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[SAT] validation error on orders/date-wise: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[SAT] upstream error on orders/date-wise: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[SAT] unexpected error on orders/date-wise")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch SAT orders. See server logs.",
        )
