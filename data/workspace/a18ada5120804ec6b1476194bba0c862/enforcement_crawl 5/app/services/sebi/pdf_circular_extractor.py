"""
app/services/sebi/pdf_circular_extractor.py
===========================================
In-memory PDF and HTML text extraction and SEBI circular-number detection.

CRITICAL STORAGE RULE:
- Never saves PDFs or HTML to disk.
- Never caches PDFs on disk.
- Processes bytes strictly in-memory or via streaming buffers, then releases them.

RESPONSIBILITIES:
- Extract text from Page 1 (and Page 2 if needed) of a PDF or HTML document in memory.
- Search extracted text for SEBI circular number patterns across modern and historical formats.
- Pass extracted circular number and/or header text to the department resolver in `departments.py`.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from app.core.http_client import COMMON_HEADERS, fetch_with_proxy
from app.services.sebi.departments import assign_department, detect_department

log = logging.getLogger(__name__)

# Max PDF/HTML size to read in memory for fallback (5 MB is plenty for inspection)
MAX_FALLBACK_BYTES = 5 * 1024 * 1024
MAX_PDF_FALLBACK_BYTES = MAX_FALLBACK_BYTES

# Timeout for fallback fetches (in seconds)
FETCH_TIMEOUT_SECONDS = 15
PDF_FETCH_TIMEOUT_SECONDS = FETCH_TIMEOUT_SECONDS

# Concurrency for fallback downloads
FALLBACK_CONCURRENCY = 5
PDF_FALLBACK_CONCURRENCY = FALLBACK_CONCURRENCY

# Canonical & historical department abbreviations for regex pattern matching
_DEPT_CODES = (
    r"(?:MRD|CFD|CFID|CDMRD|IMD|AFD|DDHS|DEPA|EAD|QJC|MIRSD|LAD|EFD|PSD|SSLD|RRD|"
    r"OIAE|GSD|ITD|IVD|ISD|OCH|VD|HRD|IID|PACL|NISM|OIA|FPIC|FITTC|FII|SMD|SMDRP|"
    r"DNPD|DMS|DSA|DOP|DP|SE|SU|PMD|DIL|MFD|RPM|DF1|DF2|CUST|IESD|MAPIN|SBD|RTA|RTAMB|DRA|DRMP|DMP)"
)

# Trailing date patterns often attached to circular numbers on headers
_DATE_TRAILER_RE = re.compile(
    r"(?i)(?:\s+|,\s*)(?:(?:dated|date|on\s+dated|as\s+on)\b|"
    r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?,?\s*(?:\d{4})?|"
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*,?\s*(?:\d{4})?).*$",
    re.I,
)

_PREFIX_CLEAN_RE = re.compile(
    r"(?i)^\s*(?:(?:circular|cir)\s*no\.?\s*[:\-]?\s*|(?:circular|cir)\s*[:\-]\s*)"
)


def clean_circular_candidate(candidate: str) -> str:
    """Clean and normalize an extracted circular number candidate."""
    if not candidate:
        return ""
    # Strip leading prefix like 'Circular No.:'
    cleaned = _PREFIX_CLEAN_RE.sub("", candidate.strip()).strip()
    # Strip trailing date
    cleaned = _DATE_TRAILER_RE.sub("", cleaned).strip(" \t:;,.-")
    # Collapse multiple whitespace
    cleaned = re.sub(r"[ \t\r\n]+", " ", cleaned)
    # Remove space surrounding slashes and hyphens
    cleaned = re.sub(r"\s*/\s*", "/", cleaned)
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    return cleaned.strip(" \t:;,.-")


def extract_circular_no_from_text(text: str) -> Optional[str]:
    """
    Search extracted text from a SEBI circular (PDF or HTML) for a SEBI circular number.

    Supported patterns include:
      - SEBI/HO/MIRSD/MIRSD-POD-1/P/CIR/2024/101
      - SEBI/HO/AFD/AFD-POD-1/P/CIR/2025/066
      - SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656
      - SEBI/HO/CDMRD/DMP/CIR/P/2016/78
      - HO/47/16/13(4)2026-MRD-POD1/I/14266/2026
      - 38/12/11(5)2026-MIRSD-POD/I/14660/2026
      - (201)2026-DDHS-POD1 I/10421/2026
      - CIR/MRD/DP/18/2015
      - CIR/CFD/DIL/1/2011
      - MRD/DoP/SE/Cir-11/2008 / MRD/DoP/SE/Cir- 06 /2008
      - SEBI/DNPD/Cir- 52 /2010
      - SEBI/HO/DMS/CIR/P/2017/15
      - IMD/HO/FPIC/CIR/P/ 2017/ 003
      - MFD/CIR No. 22 / 2311 /03
      - SMD/POLICY/Cir - 3 /03
      - SMDRP/Policy/Cir-25/2001
      - SEBI/RPM CIRCULAR NO.2 (2002-2003)
      - SEBI/ IMD/CIR No. 9/6016/2004
      - FITTC/FII/02/2002
      - MAPIN/Cir-01/2004
      - And other standard / historical formats.

    Returns the cleaned circular number string if found, or None.
    """
    if not text or not text.strip():
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Helper validator to reject arbitrary English sentences while accepting valid circular codes
    def _is_valid_candidate(s: str) -> bool:
        if not s or len(s) < 5:
            return False
        # Must contain digits (year or serial number)
        if not re.search(r"\d", s):
            return False
        # Must contain structured delimiter (/ or - or .)
        if not ("/" in s or "-" in s or "." in s):
            return False
        # Must not be a generic sentence
        words = s.split()
        if len(words) > 6 and not ("SEBI" in s.upper() or "CIR" in s.upper()):
            return False
        return True

    patterns = [
        # SEBI/..., HO/..., CIR/..., <DEPT>/...
        rf"(?i)\b((?:SEBI|HO|CIR|{_DEPT_CODES})\s*[/\-_]\s*[A-Za-z0-9/&\-_().\s]+[0-9]+[A-Za-z0-9/&\-_().]*)",
        # <DEPT>/... with letters/numbers
        rf"(?i)\b({_DEPT_CODES}\s*[/\-_]\s*[A-Za-z0-9/&\-_().\s]+)",
        # Bracketed or numeric prefix file number with embedded department
        rf"(?i)\b((?:\([0-9]+\)|[0-9]+)[0-9/&\-_().\s]*(?:SEBI|HO|CIR|{_DEPT_CODES})[A-Za-z0-9/&\-_\s().]+)",
        # Explicit circular prefix: 'Circular No. ...'
        r"(?i)^\s*(?:circular|cir\.?)\s*(?:no\.?|number)?\s*[:\-]?\s*([A-Za-z0-9&][A-Za-z0-9/&\-_().\s]+[0-9]+[A-Za-z0-9/&\-_().]*)",
    ]

    # Priority Strategy 1: Search top header lines (first 15 lines of document) for document's OWN circular number
    for line in lines[:15]:
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                candidate = m.group(1) if m.groups() else m.group(0)
                cleaned = clean_circular_candidate(candidate)
                if _is_valid_candidate(cleaned):
                    return cleaned

    # Strategy 2: Look for explicit 'Circular No.' / 'Cir No.' labeled lines anywhere
    for line in lines:
        if re.search(r"(?i)^\s*(?:circular|cir\.?)\s*(?:no\.?|number)?\s*[:\-]", line):
            cleaned = clean_circular_candidate(line)
            if _is_valid_candidate(cleaned):
                return cleaned

    # Strategy 3: Regex scanning across all lines
    for pat in patterns:
        for line in lines:
            for match in re.finditer(pat, line):
                candidate = match.group(1) if match.groups() else match.group(0)
                cleaned = clean_circular_candidate(candidate)
                if _is_valid_candidate(cleaned):
                    return cleaned

    # Strategy 4: Search across full text if line breaks split the circular number
    for pat in patterns:
        for match in re.finditer(pat, text):
            candidate = match.group(1) if match.groups() else match.group(0)
            cleaned = clean_circular_candidate(candidate)
            if _is_valid_candidate(cleaned):
                return cleaned

    return None


def extract_department_from_header_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Search document header text for an explicit full department name
    (e.g. 'MUTUAL FUNDS DEPARTMENT', 'SECONDARY MARKET DEPARTMENT').
    """
    if not text or not text.strip():
        return None, None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:25]:  # Look in top 25 header lines
        info = assign_department(None, fallback_department=line)
        if info.get("Department"):
            return info["Department"], info["Abbreviation"]

    return None, None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extract text from Page 1 (and Page 2 if needed) of a PDF in memory.
    Never writes to disk. Uses pypdfium2 as primary, pdfplumber as fallback.
    """
    if not pdf_bytes or len(pdf_bytes) < 10:
        return ""

    text = ""
    # Primary extractor: pypdfium2 (fast, native in-memory processing)
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
        try:
            pages_to_read = min(len(pdf), 2)
            for i in range(pages_to_read):
                page = pdf[i]
                textpage = page.get_textpage()
                page_text = textpage.get_text_range()
                textpage.close()
                page.close()
                if page_text:
                    text += page_text + "\n"
        finally:
            pdf.close()
    except Exception as e:
        log.debug(f"[SEBI PDF] pypdfium2 extraction failed: {e}")

    # If text is empty or very short, or if pypdfium2 had columnar interleaving, try pdfplumber
    if not text or len(text.strip()) < 40 or not extract_circular_no_from_text(text):
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                plumber_text = ""
                pages_to_read = min(len(pdf.pages), 2)
                for i in range(pages_to_read):
                    p = pdf.pages[i]
                    seen = set()
                    def _not_dup(obj):
                        if obj.get("object_type") == "char":
                            key = (round(obj["x0"], 1), round(obj["top"], 1), obj["text"])
                            if key in seen:
                                return False
                            seen.add(key)
                        return True

                    filtered_p = p.filter(_not_dup)
                    pt = filtered_p.extract_text() or p.extract_text()
                    if pt:
                        plumber_text += pt + "\n"

                if plumber_text:
                    if not extract_circular_no_from_text(text) or extract_circular_no_from_text(plumber_text):
                        text = plumber_text
        except Exception as e:
            log.debug(f"[SEBI PDF] pdfplumber fallback failed: {e}")

    return text


def extract_text_from_html_bytes(html_bytes: bytes) -> str:
    """
    Extract clean body text from HTML bytes in memory.
    Never writes to disk.
    """
    if not html_bytes:
        return ""
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        # Prefer body or main wrapper to ensure complete circular text is captured
        target = soup.body or soup
        return target.get_text()
    except Exception as e:
        log.debug(f"[SEBI HTML] HTML text extraction failed: {e}")
        return ""


def extract_circular_number_from_pdf_bytes(pdf_bytes: bytes) -> Optional[str]:
    """
    Extract circular number from page text of PDF bytes in memory.
    """
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        log.debug("[SEBI PDF] No usable text extracted from PDF (possibly scanned/image)")
        return None
    return extract_circular_no_from_text(text)


async def extract_circular_number_from_pdf_url(
    client: httpx.AsyncClient,
    pdf_url: str,
    via_proxy: bool = True,
    max_bytes: int = MAX_PDF_FALLBACK_BYTES,
    timeout: int = PDF_FETCH_TIMEOUT_SECONDS,
) -> Optional[str]:
    """
    Fetch a PDF in memory with streaming/size protection, inspect Page 1/2, and return
    the extracted SEBI circular number if found.
    """
    circular_no, _, _ = await extract_details_from_url(
        client, pdf_url, is_pdf=True, via_proxy=via_proxy, max_bytes=max_bytes, timeout=timeout
    )
    return circular_no


async def extract_details_from_url(
    client: httpx.AsyncClient,
    url: str,
    is_pdf: bool = True,
    via_proxy: bool = True,
    max_bytes: int = MAX_FALLBACK_BYTES,
    timeout: int = FETCH_TIMEOUT_SECONDS,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fetch a PDF or HTML document in memory, extract text, and return:
    (circular_number, department_name, department_abbrev).

    Guarantees:
      - Never writes document to disk.
      - Enforces size limit (max_bytes) and timeout.
      - Handles exceptions gracefully without raising.
    """
    if not url or not url.startswith("http"):
        return None, None, None

    doc_type = "PDF" if is_pdf else "HTML"
    log.info(f"[SEBI {doc_type}] Fallback triggered for: {url}")

    try:
        accept_header = "application/pdf,*/*" if is_pdf else "text/html,application/xhtml+xml,*/*"
        if via_proxy:
            resp = await fetch_with_proxy(
                client,
                url,
                method="GET",
                headers={**COMMON_HEADERS, "Accept": accept_header},
                timeout=timeout,
                via_proxy=True,
            )
            resp.raise_for_status()
            doc_bytes = resp.content[:max_bytes]
        else:
            chunks = []
            total_bytes = 0
            async with client.stream(
                "GET",
                url,
                headers={**COMMON_HEADERS, "Accept": accept_header},
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    total_bytes += len(chunk)
                    if total_bytes >= max_bytes:
                        log.warning(
                            f"[SEBI {doc_type}] Document exceeded {max_bytes} bytes for {url}; reading partial stream"
                        )
                        break
            doc_bytes = b"".join(chunks)

        if not doc_bytes:
            log.warning(f"[SEBI {doc_type}] Empty response body for {url}")
            return None, None, None

        if is_pdf:
            text = extract_text_from_pdf_bytes(doc_bytes)
        else:
            text = extract_text_from_html_bytes(doc_bytes)

        if not text:
            log.info(f"[SEBI {doc_type}] No usable text extracted from {url}")
            return None, None, None

        circular_no = extract_circular_no_from_text(text)
        dept_name, dept_abbrev = None, None

        if circular_no:
            dept_name, dept_abbrev = detect_department(circular_no)

        if not dept_name:
            # Fallback: search header lines for explicit department name
            dept_name, dept_abbrev = extract_department_from_header_text(text)

        if circular_no or dept_name:
            log.info(
                f"[SEBI {doc_type}] Extracted circular_no={circular_no!r}, "
                f"dept={dept_name!r} ({dept_abbrev}) from {url}"
            )
            return circular_no, dept_name, dept_abbrev
        else:
            log.info(f"[SEBI {doc_type}] No circular number or department found for {url}")
            return None, None, None

    except Exception as e:
        log.warning(f"[SEBI {doc_type}] Fallback extraction failed for {url}: {e}")
        return None, None, None

