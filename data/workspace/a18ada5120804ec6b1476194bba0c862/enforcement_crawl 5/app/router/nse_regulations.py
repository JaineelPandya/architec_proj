"""
app/router/nse_regulations.py
===============================
FastAPI routes for the NSE "Exchange & Market Regulations, Rules & Byelaws"
document listing.

Endpoints
---------
  GET  /health      – liveness check
  GET  /info        – valid filter values and field reference
  POST /documents   – list regulations/rules/byelaws PDFs (optionally filtered by entity)
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.nse_regulations.schema import NseRegulationsRequest
from app.services.nse_regulations.service import (
    ENTITIES,
    PAGE_URL,
    SUPPORTED_EXTENSIONS,
    fetch_nse_regulations,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    """Module liveness check."""
    log.debug("[NSE-REG] health check called")
    return {"status": "ok", "module": "nse_regulations"}


@router.get("/info")
def info():
    """Return data source info, valid filter values, and field reference."""
    log.debug("[NSE-REG] info called")
    return {
        "source": "nse_regulations",
        "source_url": PAGE_URL,
        "description": (
            "NSE Exchange & Market Regulations, Rules & Byelaws — Memorandum & "
            "Articles of Association, Rules, Byelaws, and segment-wise "
            "Regulations documents, across NSE, NSE Clearing, NSE "
            "International Exchange Ltd, and NSE International Clearing Ltd."
        ),
        "fields": [
            "entity",
            "segment",
            "title",
            "file_type",
            "size",
            "download_url",
            "type",
        ],
        "valid_entities": ENTITIES,
        "supported_file_extensions": sorted(SUPPORTED_EXTENSIONS),
        "notes": {
            "entity": (
                "Optional, case-insensitive. Must match one of the values listed "
                "above. Leave blank to return documents for all entities."
            ),
            "segment": (
                "The section heading (e.g. 'Capital Market Segment', 'Rules & "
                "Byelaws') a document is grouped under on the page. Empty string "
                "for documents listed before the first heading, such as the "
                "top-level Memorandum & Articles of Association."
            ),
            "download_url": (
                "Taken directly from the page's own link — NSE's archive "
                "storage (nsearchives.nseindia.com), served directly and "
                "without authentication."
            ),
            "known gap — NSE Clearing": (
                "NSE Clearing's document list is currently commented out in the "
                "page's HTML in favour of a link to nseclearing.in/resources/"
                "rules-and-regulations, so this entity returns zero records "
                "until NSE republishes them inline — not a scraper failure."
            ),
            "via_proxy": (
                "Defaults to false. This is a plain static content page with no "
                "observed anti-bot/session requirements; a proxy should also work."
            ),
        },
    }


@router.post("/documents")
async def documents(request: NseRegulationsRequest):
    """
    List NSE Exchange & Market Regulations, Rules & Byelaws documents.

    - **entity**: optional, case-insensitive filter — NSE | NSE Clearing |
      NSE International Exchange Ltd | NSE International Clearing Ltd.
      Leave blank to return documents for all entities.
    """
    log.info(
        f"[NSE-REG] documents request: entity={request.entity!r} "
        f"via_proxy={request.via_proxy}"
    )
    try:
        result = await fetch_nse_regulations(request)

        log.info(
            f"[NSE-REG] documents complete: entity={request.entity!r} "
            f"count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[NSE-REG] validation error on documents: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[NSE-REG] upstream error on documents: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[NSE-REG] unexpected error on documents")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch NSE regulations documents. See server logs.",
        )
