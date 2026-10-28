"""
app/services/nse/announcements/constants.py
============================================
Constants for the NSE corporate announcements API.

Ported from nse_corporate_crawler/app/nse/constants.py and adapted
to use the enforcement project's COMMON_HEADERS base.
"""

from app.core.http_client import COMMON_HEADERS

NSE_BASE_URL = "https://www.nseindia.com"

CORPORATE_ANNOUNCEMENTS_PAGE = (
    f"{NSE_BASE_URL}/companies-listing/"
    "corporate-filings-announcements"
)

CORPORATE_ANNOUNCEMENTS_API = (
    f"{NSE_BASE_URL}/api/corporate-announcements"
)

SUPPORTED_SEGMENTS = {
    "equities",
    "sme",
    "debt",
    "mf",
    "invitsreits",
}

DEFAULT_SEGMENT = "equities"

# Headers for the initial page visit (session warm-up).
PAGE_HEADERS = {
    **COMMON_HEADERS,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
}

# Headers for the JSON API call — NSE checks Referer / Origin.
API_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": CORPORATE_ANNOUNCEMENTS_PAGE,
    "Origin": NSE_BASE_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
