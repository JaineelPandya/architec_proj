"""
app/router/sebi.py
====================
FastAPI routes for the SEBI Circulars and Master Circulars scraper.

Endpoints
---------
  GET  /health           – liveness check
  GET  /info             – valid filter values and field reference
  POST /circular         – scrape SEBI circulars for a date range
  POST /master-circular  – scrape SEBI master circulars for a date range
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.sebi.schema import SebiCircularRequest, SebiMasterCircularRequest
from app.services.sebi.service import (
    CIRCULAR_DEPARTMENTS,
    CIRCULAR_PAGE_URL,
    INTERMEDIARIES,
    MASTER_CIRCULAR_DEPARTMENTS,
    MASTER_CIRCULAR_PAGE_URL,
    MAX_PAGES,
    MAX_PDF_RESOLUTIONS,
    PDF_RESOLVE_CONCURRENCY,
    scrape_sebi_circulars,
    scrape_sebi_master_circulars,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    """Module liveness check."""
    log.debug("[SEBI] health check called")
    return {"status": "ok", "module": "sebi"}


@router.get("/info")
def info():
    """Return data source info and valid filter values for both endpoints."""
    log.debug("[SEBI] info called")
    return {
        "source": "sebi",
        "source_urls": {
            "circular": CIRCULAR_PAGE_URL,
            "master_circular": MASTER_CIRCULAR_PAGE_URL,
        },
        "fields": {
            "circular": ["date", "title", "circular_no", "circular_id", "html_url", "download_url", "department", "abbreviation", "type"],
            "master_circular": ["date", "title", "circular_no", "circular_id", "html_url", "download_url", "department", "abbreviation", "type"],
        },
        "valid_department_ids": {
            "circular": CIRCULAR_DEPARTMENTS,
            "master_circular": MASTER_CIRCULAR_DEPARTMENTS,
        },
        "valid_intermediary_ids": INTERMEDIARIES,
        "notes": {
            "from_date / to_date": (
                "Required, inclusive. Formats accepted: DD-MM-YYYY or YYYY-MM-DD."
            ),
            "department / intermediary": (
                "Pass the human-readable name shown in valid_department_ids / "
                "valid_intermediary_ids ('All Department' or 'All Intermediary' for all). "
                "Not validated server-side — names are mapped to the upstream ids."
            ),
            "html_url / circular_id": (
                "html_url is the HTML landing page SEBI links to from the listing "
                "table (e.g. https://www.sebi.gov.in/legal/circulars/dec-2019/"
                "filing-of-offer-documents-...-_45272.html). circular_id is the "
                "numeric id SEBI embeds at the end of that URL (\"45272\" above), "
                "extracted for convenience; null if a landing URL doesn't match "
                "that pattern."
            ),
            "download_url": (
                "SEBI's listing table only links to an HTML circular landing "
                "page, not a direct PDF — the service follows each landing "
                "page and extracts the real PDF url from its embedded viewer "
                f"iframe, normalised to an absolute sebi.gov.in URL "
                f"({PDF_RESOLVE_CONCURRENCY} concurrent requests, capped at "
                f"{MAX_PDF_RESOLUTIONS} records per response). Falls back to "
                "the landing page URL for any record that can't be resolved "
                "(including older circulars published as plain HTML with no "
                "PDF at all)."
            ),
            "department": (
                "The canonical department full name detected via regex and dictionary "
                "lookup from the circular number (e.g. 'Corporation Finance Department')."
            ),
            "abbreviation": (
                "The canonical department abbreviation detected from the circular number "
                "(e.g. 'CFD', 'MRD', 'IMD')."
            ),
            "pagination": (
                f"Upstream paginates in pages of 25 — pages are fetched "
                f"automatically and merged, capped at {MAX_PAGES} pages as a "
                f"safety limit."
            ),
        },
    }


@router.post("/circular")
async def circular(request: SebiCircularRequest):
    """
    Scrape SEBI Circulars for a date range.

    - **from_date** / **to_date**: DD-MM-YYYY or YYYY-MM-DD, both required (inclusive)
    - **search**, **department**, **intermediary**: optional filters, passed through as-is

    Upstream pagination is walked automatically and merged into a single record list.
    """
    log.info(
        f"[SEBI] circular request: from_date={request.from_date!r} "
        f"to_date={request.to_date!r} department={request.department!r} "
        f"intermediary={request.intermediary!r} via_proxy={request.via_proxy}"
    )
    try:
        result = await scrape_sebi_circulars(request)

        log.info(
            f"[SEBI] circular complete: from_date={request.from_date!r} "
            f"to_date={request.to_date!r} count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[SEBI] validation error on circular: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[SEBI] upstream error on circular: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[SEBI] unexpected error on circular")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch SEBI circulars. See server logs.",
        )


@router.post("/master-circular")
async def master_circular(request: SebiMasterCircularRequest):
    """
    Scrape SEBI Master Circulars for a date range.

    - **from_date** / **to_date**: DD-MM-YYYY or YYYY-MM-DD, both required (inclusive)
    - **department**: optional filter; omit or leave blank for all departments

    Upstream pagination is walked automatically and merged into a single record list.
    """
    log.info(
        f"[SEBI] master-circular request: from_date={request.from_date!r} "
        f"to_date={request.to_date!r} department={request.department!r} "
        f"via_proxy={request.via_proxy}"
    )
    try:
        result = await scrape_sebi_master_circulars(request)

        log.info(
            f"[SEBI] master-circular complete: from_date={request.from_date!r} "
            f"to_date={request.to_date!r} count={result.get('count')}"
        )
        return result

    except ValueError as exc:
        log.warning(f"[SEBI] validation error on master-circular: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        log.error(f"[SEBI] upstream error on master-circular: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("[SEBI] unexpected error on master-circular")
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch SEBI master circulars. See server logs.",
        )
