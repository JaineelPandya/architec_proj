"""
app/services/sebi/service.py
==============================
Scraper for the SEBI Circulars and Master Circulars listing pages at
https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid={7|6}&smid=0

Verified live behaviour (from direct HTML/JS inspection of entry.js ->
searchFormNewsList, the handler wired to the site's search form):
  - GET  /sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=<7|6>&smid=0
                          – loads the search form. ssid=7 -> Circulars,
                            ssid=6 -> Master Circulars.
  - POST /sebiweb/ajax/home/getnewslistinfo.jsp
                          – form fields: nextValue, next ('s' fresh search /
                            'n' page navigation), search, fromDate, toDate
                            (DD-MM-YYYY), fromYear, toYear, deptId, sid=1,
                            ssid, smid=0, ssidhidden, intmid, sText=Legal,
                            ssText, smText, doDirect (0-based target page
                            index, used together with next='n').
                          – Returns "<listing html>#@#<breadcrumb html>";
                            only the part before '#@#' is used here.
  - The endpoint is stateless: pages can be requested directly via doDirect
    without replaying a session/cookie or the nextValue returned by a prior
    response (verified — requesting doDirect=1 directly returns "26 to 50"
    without first requesting page 1). Results are paginated 25 per page; the
    total record count is parsed from the "X to Y of N records" text.
  - The listing table only exposes Date + Title (linking to an HTML
    circular/master-circular landing page, not a raw PDF) — there is no
    per-row Department column. The `department` field on each record
    therefore reflects the deptId filter applied to the request rather
    than scraped per-row data; the department is inferred via regex from
    the circular number prefix (e.g. MRD, CFD, IMD) at no extra network cost.
  - Each landing page embeds the actual document in an iframe:
    `<iframe src='.../web/?file=<download_url>'>` (verified on both a Circular and
    a Master Circular landing page). `download_url` on each record is resolved by
    fetching the landing page and extracting that `file=` value (normalised to an
    absolute sebi.gov.in URL), bounded by a concurrency limit and a safety cap
    (falls back to the landing page URL itself if resolution fails for a given
    record). The landing page URL itself is also returned per record as `html_url`,
    along with `circular_id` — the numeric id suffix SEBI embeds in that URL
    (e.g. `..._45272.html` -> `"45272"`).
"""

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import unquote
import httpx
from bs4 import BeautifulSoup

from app.core.cache import TTLCache
from app.core.http_client import fetch_with_proxy, COMMON_HEADERS
from app.services.sebi.departments import assign_department
from app.services.sebi.pdf_circular_extractor import (
    FALLBACK_CONCURRENCY,
    PDF_FALLBACK_CONCURRENCY,
    extract_circular_no_from_text,
    extract_circular_number_from_pdf_url,
    extract_details_from_url,
    extract_text_from_html_bytes,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.sebi.gov.in"
AJAX_URL = f"{BASE_URL}/sebiweb/ajax/home/getnewslistinfo.jsp"
CIRCULAR_PAGE_URL = f"{BASE_URL}/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0"
MASTER_CIRCULAR_PAGE_URL = f"{BASE_URL}/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0"

PAGE_SIZE = 25
MAX_PAGES = 200  # safety cap: 200 x 25 = 5000 records per request

PDF_RESOLVE_CONCURRENCY = 10   # concurrent landing-page fetches while resolving download_url
MAX_PDF_RESOLUTIONS = MAX_PAGES * PAGE_SIZE  # records beyond this keep the landing-page URL — matches
                                              # the listing's own MAX_PAGES cap, so nothing returned by
                                              # the listing walk is left unresolved underneath it


TTL_SHORT_S = 10 * 60        # 10 minutes — to_date is today or future
TTL_LONG_S = 7 * 24 * 3600   # 7 days    — to_date is fully in the past

# Module-level singleton: shared across all calls within the process.
_cache: TTLCache = TTLCache(default_ttl_seconds=TTL_SHORT_S)

# Reference data only (surfaced via GET /sebi/info) — captured from the live
# deptId / intmid <select> options on the Circulars and Master Circulars
# pages. Not enforced on requests: SEBI can add departments/intermediaries
# over time and deptId/intmid are passed through to the upstream form as-is.
CIRCULAR_DEPARTMENTS: dict[str, str] = {
    "-1": "All Department",
    "75": "Alternative Investment Fund and Foreign Portfolio Investors Department",
    "1": "Corporation Finance Department",
    "3": "Department Economic and Policy Analysis",
    "64": "Department of Debt and Hybrid Securities",
    "6": "Enforcement Department - 1",
    "35": "Information Technology Department",
    "9": "Investment Management Department",
    "14": "Market Intermediaries Regulation and Supervision Department",
    "15": "Market Regulation Department",
    "19": "Office of Investor Assistance and Education",
}

MASTER_CIRCULAR_DEPARTMENTS: dict[str, str] = {
    "-1": "All Department",
    "75": "Alternative Investment Fund and Foreign Portfolio Investors Department",
    "1": "Corporation Finance Department",
    "3": "Department Economic and Policy Analysis",
    "64": "Department of Debt and Hybrid Securities",
    "6": "Enforcement Department - 1",
    "10": "Integrated Surveillance Department",
    "9": "Investment Management Department",
    "14": "Market Intermediaries Regulation and Supervision Department",
    "15": "Market Regulation Department",
    "19": "Office of Investor Assistance and Education",
}

INTERMEDIARIES: dict[str, str] = {
    "-1": "All Intermediary",
    "5": "Banker to an Issue",
    "6": "Debentures Trustee",
    "7": "Credit Rating Agency - CRA",
    "8": "KYC (Know Your Client) Registration Agency registered with SEBI",
    "9": "Merchant Bankers",
    "10": "Registrars to an issue and share Transfer Agents",
    "11": "Underwriters",
    "16": "Registered Alternative Investment Funds",
    "21": "Registered Venture Capital Funds",
    "23": "Registered Mutual Funds",
    "25": "Registered Foreign Venture Capital Investors",
    "27": "Registered Custodians",
    "28": "Deemed FPIs (Erstwhile Sub-Accounts)",
    "29": "FPIs / Deemed FPIs (Erstwhile FIIs/QFIs)",
    "30": "Registered Stock Brokers in equity segment",
    "31": "Registered Stock Brokers in Equity Derivative Segment",
    "32": "Registered Stock Brokers in Currency Derivative Segment",
    "33": "Registered Portfolio Managers",
    "34": "Self-Certified Syndicate Banks under the direct ASBA facility (equity issuances)",
}

CIRCULAR_DEPARTMENT_NAMES_TO_ID: dict[str, str] = {
    label: value for value, label in CIRCULAR_DEPARTMENTS.items()
}
MASTER_CIRCULAR_DEPARTMENT_NAMES_TO_ID: dict[str, str] = {
    label: value for value, label in MASTER_CIRCULAR_DEPARTMENTS.items()
}
INTERMEDIARY_NAMES_TO_ID: dict[str, str] = {
    label: value for value, label in INTERMEDIARIES.items()
}

# Per-listing-type config shared by both public scraper functions below.
_CIRCULAR = {
    "ssid": "7",
    "ssText": "Circulars",
    "page_url": CIRCULAR_PAGE_URL,
    "result_type": "Circular",
    "dept_map": CIRCULAR_DEPARTMENTS,
    "include_search": True,
    "include_intermediary": True,
    "annotate_department": True,
}
_MASTER_CIRCULAR = {
    "ssid": "6",
    "ssText": "Master Circulars",
    "page_url": MASTER_CIRCULAR_PAGE_URL,
    "result_type": "Master Circular",
    "dept_map": MASTER_CIRCULAR_DEPARTMENTS,
    "include_search": False,
    "include_intermediary": False,
    "annotate_department": True,
}

_RECORD_COUNT_RE = re.compile(r"of\s+([\d,]+)\s+records")
_PDF_FILE_RE = re.compile(r'''<iframe[^>]*\bsrc=["'][^"']*[?&]file=([^"'&]+)''', re.I)
_LANDING_ID_RE = re.compile(r'_(\d+)\.html?$', re.I)

# ---------------------------------------------------------------------------
# Manual overrides for legacy scanned or font-unmapped SEBI circulars
# (where PDF has 0 digital text or stripped font cmaps, keyed by circular_id/attachdoc ID)
# ---------------------------------------------------------------------------
KNOWN_CIRCULAR_OVERRIDES: dict[str, Optional[str]] = {
    # Original 5 historical scanned / unmapped
    "25126": "CIR/IMD/FIIC/11/2013",                 # Utilisation period for Government Debt Limits (31-07-2013)
    "23734": "CIR/IMD/FIIC/22/2012",                 # Debt Allocation Mechanism for FII (07-11-2012)
    "34347": "SEBI/HO/MIRSD/MIRSD6/CIR/P/2017/20",    # Redressal of complaints against Stock Brokers (10-03-2017)
    "30526": "CIR/MIRSD/2/2015",                     # Implementation of MCAA and FATCA (26-08-2015)
    "24632": "CIR/OIAE/1/2013",                      # Redress of investor grievances through SCORES (17-04-2013)
    # attachdoc filename ID -> circular_no
    "1375265773238": "CIR/IMD/FIIC/11/2013",
    "1352289200241": "CIR/IMD/FIIC/22/2012",
    "1489143609606": "SEBI/HO/MIRSD/MIRSD6/CIR/P/2017/20",
    "1440580845807": "CIR/MIRSD/2/2015",
    "1366201086643": "CIR/OIAE/1/2013",

    # Non-circular reference tables / compendiums -> Explicit None (prevents messy text snippet extraction)
    "6507": None,                                    # List of Million Plus Cities (Census data, not a circular)
    "6504": None,                                    # List of Million Plus UAs Cities (Census data, not a circular)
    "14641": None,                                   # Portfolio Managers - Circulars 2006 (Annual compendium index)
    "15543": None,                                   # Mutual Funds Circulars-2004 (Annual compendium index)
    "15994": None,                                   # Portfolio Managers - Circulars 2003 (Annual compendium index)
    "16465": None,                                   # Circulars Issued By SEBI For Portfolio Managers Year-Wise (Compendium)
    "17453": None,                                   # Mutual Funds Circulars-2000 (Annual compendium index)

    # Historical circulars with confirmed numbers
    "6168": "Circular No. 2/2008",                               # CBDT circular on Securities Lending/Borrowing (22-02-2008)
    "22359": "SEBI/CBM/BOND/2/2007/13/04",                       # Corporate Bond Market-Launch of Trading Platform (13-04-2007)
    "8995": "SEBI/CBM/BOND/1/2007/01/03",                        # Corporate Bond Market - Reporting Platform to also be set up by NSE (02-03-2007)
    "8356": "DPS-II/GTB-ECS/39066/2005",                         # Withdrawal of temporary exemption from ECS facility (26-04-2005)
    "15863": "RMB (Compendium) Series Circular No. 9",           # Amendments to SEBI (DIP) Guidelines, 2000 (02-05-2003)
    "15825": "D&CC/FITTC/CIR-18/2003",                           # Circular no. 18 of Depository & custodian cell (14-02-2003)
    "16865": "D&CC/FITTC/CIR-16/2002",                           # SECRETARIAL AUDIT (31-12-2002)
    "16482": "RMB Circular No. 1 (2002-2003)",                   # RMB CIRCULAR NO.1 (2002-2003) (17-09-2002)
    "16479": "RPM Circular No. 1 (2002-2003)",                   # RPM Circular No.1 (2002-2003) (17-09-2002)
    "16437": "D&CC/FITTC/CIR-09/2002",                           # Circular no 9 - Separate DR account (04-07-2002)
    "16166": "D&CC/FITTC/CIR-07/2002",                           # Depositories & Custodial Division - FITTC Circular No. 7 (08-04-2002)
    "16126": "RMB (Compendium) Series Circular No. 3 (2001-2002)",# Amendments to SEBI (DIP) Guidelines, 2000 (11-01-2002)
    "17790": "RMB (Compendium) Series Circular No. 2 (2001-2002)",# RMB (Compendium) Series Circular No. 2 (29-11-2001)
    "17792": "SMDRPD/Policy/Cir-50/2001",                        # Confidentiality of client information (08-11-2001)
    "17992": "SMDRPD/Policy/Cir-35/2001",                        # Withdrawal of restriction on short sales (25-06-2001)
    "18020": "RTI Circular No. 1 (2000-2001)",                   # RTI Circular No.1 (2000-2001) (09-05-2001)
    "17539": "DIP (Compendium) Series Circular No. 5",           # Guidelines for offering securities in public issues (30-11-2000)
    "13539": "DIP (Compendium) Series Circular No. 3",           # Amendments to SEBI (DIP) Guidelines, 2000 (04-08-2000)
    "17700": "IES/DC/9863/00",                                   # Daily reports for trading and settlement of derivative trades (20-06-2000)
    "103721": "HO/24/11/36(24)2026-IMD-RAC4/I/18849/2026",       # Revision of Application Form for Mutual Fund Registration (17-08-2026)
    "96549": "SEBI/HO/AFD/AFD-PoD-3/P/CIR/2025/127",             # Ease of regulatory compliances for FPIs (10-09-2025)
    "93980": "SEBI/HO/AFD/AFD-POD-3/P/CIR/2025/71",              # Extension of timeline for ODIs/FPIs (16-05-2025)
    "88408": "SEBI/HO/AFD/AFD-PoD-3/P/CIR/2024/156",             # Simplified registration for FPIs (12-11-2024)
    "87323": "SEBI/HO/CFD/CFD-PoD-2/P/CIR/2024/133",             # Relaxation from LODR provisions (03-10-2024)
    "87032": "SEBI/HO/AFD/AFD-PoD-3/P/CIR/2024/130",             # Operational Guidelines for FVCIs (26-09-2024)
    "86714": "CIR/CFD/PoD/2024/122",                             # Enabling T+2 trading of Bonus shares (16-09-2024)

    # Master Circulars
    "100491": "HO/24/13/11(1)2026-IMD-POD-1/I/7602/2026",        # Master Circular for Mutual Funds (20-03-2026)
    "84441": "SEBI/HO/IMD/IMD-PoD-1/P/CIR/2024/90",              # Master Circular for Mutual Funds (27-06-2024)
    "77789": "SEBI/HO/MRD/MRD-PoD-2/P/CIR/2023/166",             # Master Circular for Depositories (06-10-2023)
    "73795": "SEBI/HO/CFD/PoD2/CIR/P/2023/120",                  # Master Circular for LODR compliance by listed entities (11-07-2023)
    "71265": "SEBI/HO/MIRSD/MIRSD-PoD-1/P/CIR/2023/71",          # Master Circular for Stock Brokers (17-05-2023)
    "69155": "SEBI/HO/IMD/IMD-POD-1/P/CIR/2023/38",              # Master Circular for Portfolio Managers (20-03-2023)
    "39166": "SEBI/HO/MIRSD/DOP1/CIR/P/2018/87",                 # Master Circular for Stock Brokers (01-06-2018)

    # 2020-2021 FPI & IFSC circulars
    "53798": "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656",              # Write-off of debt securities held by FPIs (08-11-2021)
    "51628": "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 609",              # Modification in Operational Guidelines for FPIs (04-08-2021)
    "50380": "SEBI/HO/FPI&C/P/CIR/2021/0569",                   # Off-market transfer of securities by FPI (01-06-2021)
    "47446": "SEBI/HO/FPI&C/CIR/P/2020/162",                    # Temporary relaxation in processing of FPI docs (31-08-2020)
    "47034": "SEBI/HO/IFSC/CIR/P/2020/117",                     # SEBI (IFSC) Guidelines 2015 - Amendments (09-07-2020)
    "46455": "SEBI/HO/FPI&C/CIR/P/2020/056",                    # Temporary relaxation in processing of FPI docs (30-03-2020)
    "36012": "SEBI/HO/MIRSD/MIRSD1/CIR/P/2017/104",             # Integration of broking activities (21-09-2017)
    "35452": "SEBI/HO/CIR/P/2017/85",                           # SEBI (IFSC) Guidelines 2015 - Amendments (27-07-2017)
    "35287": "SEBI/HO/CIR/P/2017/79",                           # Guidelines for participation of EFIs and FPIs in IFSC (11-07-2017)
    "23820": "CIR/DRMNP/4/2012",                                # Mini derivative contract on Index (20-11-2012)
    "20785": "SEBI/Cir/ISD/ 05 /2011",                          # Clarification on 100% promoter holding in demat form (30-09-2011)

    # 2004-2008 historical confirmed circulars
    "6461": "SEBI/IMD/CIR No.1/122201/08",                      # Revision in Filing/Registration Fees - Mutual Funds (03-04-2008)
    "8308": "SEBI/VCF/Cir no. 1/ 98645 /2007",                  # Guidelines for Overseas Investments by VCFs (09-08-2007)
    "14652": "SEBI/DoF/VCF/CIR No. 1/78911/2006",               # Common Key Personnel between MFs and VCFs (18-10-2006)
    "14694": "SEBI/IMD/CIR No.6/63715/06",                      # Applicability of Investment Restrictions for Securitised Debt (29-03-2006)
    "11315": "CRD/DCR/DLT/HB/Cir-1/06",                         # Amendment to SEBI (Delisting of Securities) Guidelines 2003 (31-01-2006)
    "8340": "MAPIN/Cir – 1 /2005",                              # SEBI (MAPIN) Regulations 2003 - Clarifications (04-01-2005)
    "13151": "MAPIN/Cir – 37 /2004",                            # SEBI (MAPIN) Regulations 2003 (27-10-2004)
    "12856": "MRD/DOP/MAPIN/Cir-26/2004",                       # SEBI (MAPIN) Regulations 2003 (16-08-2004)
    "9550": "MIR&SD/DR-1/MSS/Cir-08/2004",                      # Participation of Banks in Interest Rate Derivatives Market (20-01-2004)

    # 2002-2003 historical confirmed circulars
    "15875": "RMB (Compendium) Series Circular No. 10",         # Amendments to SEBI (DIP) Guidelines 2000 (02-06-2003)
    "15830": "DCC/FITTC/CIR-19/2003",                           # Implementation of T+2 rolling settlement - Depositories (04-03-2003)
    "15829": "D&CC/FITTC/CIR-20/2003",                          # Implementation of T+2 rolling settlement - Custodians (04-03-2003)
    "15826": "LGL/Cir-2/2003",                                  # Approval of Amendments to Bye Laws/Rules (19-02-2003)
    "16753": "SEBI/RUW Circular No. 2(2002-2003)/UW",           # Application procedure for registration as Underwriter (17-12-2002)
    "16737": "D&CC/FITTC/CIR-14/2002",                          # Depositories and Custodian Division Circular No. 14 (15-11-2002)
    "16703": "RMB Circular no. 2 (2002-03)/SAST",               # Post offer public announcement - SAST (18-10-2002)
    "16668": "RRTI Circular No 1. (2002-2003)",                 # Registrars to an Issue Circular No. 1 (25-09-2002)
    "16667": "RUW CIRCULAR NO.1 (2002-2003)",                   # Underwriters Circular No. 1 (23-09-2002)
    "16476": "SMD/DBA–II/CIR-22/2002",                          # Secondary Market Department Circular No. 22 (12-09-2002)

    # 2001 historical confirmed circulars
    "17952": "DCC/FITTCIR-3//2001",                             # FITTC Circular No. 3 - ECS facility (15-10-2001)
    "17962": "SMDRPD/Policy/Cir-43/2001",                       # Rescheduling of settlements due to bank holidays (20-08-2001)
    "17981": "RMB (Compendium) Series Circular No. 1 (2001- 2002)", # Amendments to SEBI (DIP) Guidelines 2000 (17-07-2001)
    "17986": "SMDRPD/Policy/Cir-37 /2001",                      # Index based market wide circuit breaker (28-06-2001)
    "18006": "SMDRPD/Policy/Cir-30/2001",                       # Compulsory Rolling Settlement (24-05-2001)
    "18014": "BTI (G I Series) Circular No. 1 (2000- 2001)",    # Bankers to an Issue Circular No. 1 (11-05-2001)
    "18015": "BTI (G I Series) Circular No. 1 (2000- 2001)",    # Bankers to an Issue Circular No. 1 (11-05-2001)
    "18072": "IES/VCF/Cir-1/2001",                              # Reporting of Venture Capital Activity (12-02-2001)
    "18078": "SMDRPD/Policy/Cir-8/2001",                        # Composition of deposit with subsidiary (07-02-2001)
    "18081": "SMDRPD/Policy/Cir-6/2001",                        # Meeting of all Stock Exchanges (01-02-2001)
    "18085": "SUB-BROK/CIR/02/2001",                            # Intimation to brokers for sub-broker registration (15-01-2001)

    # 2000 historical confirmed circulars
    "17521": "IES/DC/CIR- 5/00",                                # Risk containment measures for Option on Indices (11-12-2000)
    "17546": "SMDRPD/Policy/Cir-53/2000",                       # Risk Containment measures for rolling settlement (15-11-2000)
    "17548": "SMDRPD/Policy/Cir-51/2000",                       # Group on Risk Management in Equity Markets (06-11-2000)
    "17551": "SMDRPD/Policy/Cir-52/2000",                       # Group on Risk Management in Equity Markets (06-11-2000)
    "17607": "DIP (Compendium) Circular No. 4",                 # Amendments to SEBI (DIP Guidelines), 2000 (06-09-2000)
    "17612": "DT CIRCULAR NO. 1( 2000 - 2001)",                 # Debenture Trustees Second Amendment Regulations (05-09-2000)
    "17651": "MF/CIR/ 07/ 404/2000",                            # Monthly Cumulative Report (MCR) (31-07-2000)
    "17712": "IES/DC/ 8726/00",                                 # Circular No. 8726 (31-05-2000)
    "17708": "PMIMD/8755/2000",                                 # Relaxation of Rule 19(2)(b) of SCRA (31-05-2000)
    "38697": "DT CIRCULAR NO.3(1999-2000)",                     # Debenture Trustees Amendment Regulations (14-03-2000)
    "17936": "RMB (Compendium) Series Circular No. 2 (1999- 2000)", # Amendments to SEBI (DIP) Guidelines (16-02-2000)
    "18002": "RMB (Compendium) Series Circular No. 1 (1999-2000)",  # Amendments to SEBI (DIP) Guidelines (19-01-2000)

    # attachdoc filename ID -> circular_no mappings
    "1636363238961": "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656",
    "1628076322017": "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 609",
    "1622544446670": "SEBI/HO/FPI&C/P/CIR/2021/0569",
    "1598872791117": "SEBI/HO/FPI&C/CIR/P/2020/162",
    "1594288318159": "SEBI/HO/IFSC/CIR/P/2020/117",
    "1585563635131": "SEBI/HO/FPI&C/CIR/P/2020/056",
    "1509185893314": "SEBI/HO/MIRSD/MIRSD1/CIR/P/2017/104",
    "1511176443393": "SEBI/HO/CIR/P/2017/85",
    "1509427029608": "SEBI/HO/CIR/P/2017/79",
    "1353409729402": "CIR/DRMNP/4/2012",
    "1317635543075": "SEBI/Cir/ISD/ 05 /2011",
    "1291097574569": None,
    "1291097480076": None,
    "1291093182434": "SEBI/IMD/CIR No.1/122201/08",
    "1291703073278": "SEBI/VCF/Cir no. 1/ 98645 /2007",
    "1292487267253": "CRD/DCR/DLT/HB/Cir-1/06",
    "1293167165438": "MAPIN/Cir – 37 /2004",
    "1293079991728": "MRD/DOP/MAPIN/Cir-26/2004",
    "1300871294528": "RMB (Compendium) Series Circular No. 1 (2001- 2002)",
    "1300873149820": "SMDRPD/Policy/Cir-30/2001",
    "1300880035440": "SMDRPD/Policy/Cir-6/2001",
    "1300707082606": "IES/DC/CIR- 5/00",
    "1300710025531": "SMDRPD/Policy/Cir-53/2000",
    "1300710166356": "SMDRPD/Policy/Cir-51/2000",
    "1300774518732": "DIP (Compendium) Circular No. 4",
    "1300780857365": "IES/DC/ 8726/00",

    # Master Circulars attachdoc IDs
    "1774024028162": "HO/24/13/11(1)2026-IMD-POD-1/I/7602/2026",
    "1719488538007": "SEBI/HO/IMD/IMD-PoD-1/P/CIR/2024/90",
    "1696831738330": "SEBI/HO/MRD/MRD-PoD-2/P/CIR/2023/166",
    "1689087475880": "SEBI/HO/CFD/PoD2/CIR/P/2023/120",
    "1684323080627": "SEBI/HO/MIRSD/MIRSD-PoD-1/P/CIR/2023/71",
    "1680688275651": "SEBI/HO/IMD/IMD-POD-1/P/CIR/2023/38",
    "1527862124221": "SEBI/HO/MIRSD/DOP1/CIR/P/2018/87",
}

KNOWN_DEPARTMENT_OVERRIDES: dict[str, tuple[Optional[str], Optional[str]]] = {
    # circular_id -> (department, abbreviation)
    "6168": ("Market Regulation Department", "MRD"),
    "22359": ("Market Regulation Department", "MRD"),
    "8995": ("Market Regulation Department", "MRD"),
    "14641": ("Investment Management Department", "IMD"),
    "8356": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "15543": ("Investment Management Department", "IMD"),
    "15994": ("Investment Management Department", "IMD"),
    "15863": ("Corporation Finance Department", "CFD"),
    "15825": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "16865": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "16482": ("Corporation Finance Department", "CFD"),
    "16479": ("Investment Management Department", "IMD"),
    "16465": ("Investment Management Department", "IMD"),
    "16437": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "16166": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "16126": ("Corporation Finance Department", "CFD"),
    "17790": ("Corporation Finance Department", "CFD"),
    "17792": ("Market Regulation Department", "MRD"),
    "17992": ("Market Regulation Department", "MRD"),
    "18020": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "17539": ("Corporation Finance Department", "CFD"),
    "13539": ("Corporation Finance Department", "CFD"),
    "17700": ("Integrated Surveillance Department", "ISD"),
    "103721": ("Investment Management Department", "IMD"),
    "96549": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "93980": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "88408": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "87323": ("Corporation Finance Department", "CFD"),
    "87032": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "86714": ("Corporation Finance Department", "CFD"),

    # Master Circulars
    "100491": ("Investment Management Department", "IMD"),
    "84441": ("Investment Management Department", "IMD"),
    "77789": ("Market Regulation Department", "MRD"),
    "73795": ("Corporation Finance Department", "CFD"),
    "71265": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "69155": ("Investment Management Department", "IMD"),
    "39166": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),

    # 2020-2021 FPI & IFSC
    "53798": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "51628": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "50380": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "47446": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "47034": ("Market Regulation Department", "MRD"),
    "46455": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "36012": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "35452": ("Market Regulation Department", "MRD"),
    "35287": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "23820": ("Market Regulation Department", "MRD"),
    "20785": ("Integrated Surveillance Department", "ISD"),

    # 2004-2008 historical
    "6507": (None, None),
    "6504": (None, None),
    "6461": ("Investment Management Department", "IMD"),
    "8308": ("Investment Management Department", "IMD"),
    "14652": ("Investment Management Department", "IMD"),
    "14694": ("Investment Management Department", "IMD"),
    "11315": ("Corporation Finance Department", "CFD"),
    "8340": ("Market Regulation Department", "MRD"),
    "13151": ("Market Regulation Department", "MRD"),
    "12856": ("Market Regulation Department", "MRD"),
    "9550": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),

    # 2002-2003 historical
    "15875": ("Corporation Finance Department", "CFD"),
    "15830": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "15829": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "15826": ("Legal Affairs Department", "LAD"),
    "16753": ("Corporation Finance Department", "CFD"),
    "16737": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "16703": ("Corporation Finance Department", "CFD"),
    "16668": ("Corporation Finance Department", "CFD"),
    "16667": ("Corporation Finance Department", "CFD"),
    "16476": ("Market Regulation Department", "MRD"),

    # 2001 historical
    "17952": ("Market Intermediaries Regulation and Supervision Department", "MIRSD"),
    "17962": ("Market Regulation Department", "MRD"),
    "17981": ("Corporation Finance Department", "CFD"),
    "17986": ("Market Regulation Department", "MRD"),
    "18006": ("Market Regulation Department", "MRD"),
    "18014": ("Corporation Finance Department", "CFD"),
    "18015": ("Corporation Finance Department", "CFD"),
    "18072": ("Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
    "18078": ("Market Regulation Department", "MRD"),
    "18081": ("Market Regulation Department", "MRD"),
    "18085": ("Market Regulation Department", "MRD"),

    # 2000 historical
    "17453": ("Investment Management Department", "IMD"),
    "17521": ("Integrated Surveillance Department", "ISD"),
    "17546": ("Market Regulation Department", "MRD"),
    "17548": ("Market Regulation Department", "MRD"),
    "17551": ("Market Regulation Department", "MRD"),
    "17607": ("Corporation Finance Department", "CFD"),
    "17612": ("Corporation Finance Department", "CFD"),
    "17651": ("Investment Management Department", "IMD"),
    "17712": ("Integrated Surveillance Department", "ISD"),
    "17708": ("Corporation Finance Department", "CFD"),
    "38697": ("Corporation Finance Department", "CFD"),
    "17936": ("Corporation Finance Department", "CFD"),
    "18002": ("Corporation Finance Department", "CFD"),
}



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


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_table(html: str) -> list[dict]:
    """
    Parse the listing fragment into normalised row dicts.

    Table columns: Date | Title (anchor -> circular/master-circular landing page)
    """
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    rows: list[dict] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue  # header row uses <th>, not <td>

        date_text = cells[0].get_text(strip=True)
        anchor = cells[1].find("a")
        if anchor is None:
            continue

        rows.append({
            "date": date_text,
            "title": anchor.get_text(strip=True),
            "url": anchor.get("href", "").strip(),
        })

    return rows


def _parse_record_count(html: str) -> int | None:
    """Extract the total record count from the 'X to Y of N records' pagination text."""
    match = _RECORD_COUNT_RE.search(html or "")
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


# ---------------------------------------------------------------------------
# Core HTTP flow
# ---------------------------------------------------------------------------

async def _fetch_page(
    client: httpx.AsyncClient,
    listing: dict,
    search: str,
    from_str: str,
    to_str: str,
    dept_id: str,
    intmid: str,
    page_index: int,
    via_proxy: bool,
) -> tuple[list[dict], int | None]:
    """POST one page of the listing AJAX endpoint. page_index is 0-based."""
    data = {
        "nextValue": "1",
        "next": "s" if page_index == 0 else "n",
        "search": search,
        "fromDate": from_str,
        "toDate": to_str,
        "fromYear": "",
        "toYear": "",
        "deptId": dept_id or "-1",
        "sid": "1",
        "ssid": listing["ssid"],
        "smid": "0",
        "ssidhidden": listing["ssid"],
        "intmid": intmid or "-1",
        "sText": "Legal",
        "ssText": listing["ssText"],
        "smText": "",
        "doDirect": "-1" if page_index == 0 else str(page_index),
    }
    resp = await fetch_with_proxy(
        client,
        AJAX_URL,
        method="POST",
        data=data,
        headers={
            **COMMON_HEADERS,
            "Referer": listing["page_url"],
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
        via_proxy=via_proxy,
    )
    resp.raise_for_status()

    listing_html = resp.text.split("#@#", 1)[0]
    return _parse_table(listing_html), _parse_record_count(listing_html)



async def _resolve_landing_details(client: httpx.AsyncClient, landing_url: str, via_proxy: bool) -> dict:
    """
    Follow a circular/master-circular landing page and extract the real PDF
    URL from its embedded viewer iframe (`.../web/?file=<download_url>`) and
    the Circular No. if available.

    Falls back to the landing page URL itself on any failure (network error,
    missing iframe, unexpected markup) — a single bad record must not fail
    the whole listing.
    """
    result = {"download_url": landing_url, "circular_no": None}
    if not landing_url:
        return result
    try:
        resp = await fetch_with_proxy(
            client,
            landing_url,
            method="GET",
            headers={**COMMON_HEADERS, "Referer": BASE_URL},
            timeout=20,
            via_proxy=via_proxy,
        )
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract download URL
        pdf_url = None
        match = _PDF_FILE_RE.search(resp.text)
        if match:
            pdf_url = unquote(match.group(1))
        
        # Secondary check if regex didn't find iframe with ?file=
        if not pdf_url:
            for tag in soup.find_all(["iframe", "embed", "object", "a"]):
                src = tag.get("src") or tag.get("data") or tag.get("href") or ""
                if not src:
                    continue
                if "file=" in src:
                    m = re.search(r'[?&]file=([^"\'&]+)', src)
                    if m:
                        pdf_url = unquote(m.group(1))
                        break
                if src.lower().endswith(".pdf") or "/attachdocs/" in src.lower():
                    pdf_url = unquote(src)
                    break

        if pdf_url:
            if pdf_url.startswith("/"):
                pdf_url = BASE_URL + pdf_url
            result["download_url"] = pdf_url
            
        # Extract Circular No.
        raw_circular_no = None
        
        id_area = soup.find("div", class_="id_area")
        if id_area:
            spans = id_area.find_all("span")
            if len(spans) >= 2:
                label_text = spans[0].get_text(strip=True)
                if re.search(r'(?i)(?:circular|cir\.?)\s*no', label_text):
                    raw_circular_no = spans[1].get_text(strip=True)
                    
        # Fallback for older pages where Circular No is in text or paragraphs,
        # or when raw_circular_no from id_area is only a short number without department letters
        if not raw_circular_no or not re.search(r"[a-zA-Z]", raw_circular_no):
            html_text = extract_text_from_html_bytes(resp.content)
            extracted_cno = extract_circular_no_from_text(html_text)
            if extracted_cno:
                raw_circular_no = extracted_cno

        if raw_circular_no:
            # Edge case 1: Remove "Circular No.:" or "Cir. No." prefix if it accidentally bled into this span
            clean_no = re.sub(r'(?i)^(?:(?:circular|cir\.?)\s*no\.?\s*:?)\s*', '', raw_circular_no)
            # Edge case 2: Collapse any multiple spaces/newlines into a single space
            clean_no = re.sub(r'\s+', ' ', clean_no).strip(" \t\n\r:;,.-")
            if clean_no.startswith("(") and clean_no.endswith(")"):
                clean_no = clean_no[1:-1].strip()
            result["circular_no"] = clean_no

        # Check known manual overrides first (supersedes any noisy page text extractions)
        id_match = _LANDING_ID_RE.search(landing_url or "")
        if id_match and id_match.group(1) in KNOWN_CIRCULAR_OVERRIDES:
            result["circular_no"] = KNOWN_CIRCULAR_OVERRIDES[id_match.group(1)]
        elif not result["circular_no"] and result["download_url"]:
            for key, val in KNOWN_CIRCULAR_OVERRIDES.items():
                if key in result["download_url"]:
                    result["circular_no"] = val
                    break
                
    except Exception:
        log.debug(f"[SEBI] could not resolve landing details for {landing_url}", exc_info=True)
    return result


async def _resolve_landing_details_batch(
    client: httpx.AsyncClient, landing_urls: list[str], via_proxy: bool
) -> list[dict]:
    """
    Resolve real PDF URLs and Circular Nos for a batch of landing pages, bounded by
    PDF_RESOLVE_CONCURRENCY concurrent requests and capped at
    MAX_PDF_RESOLUTIONS — records beyond the cap keep the landing-page URL and None for circular_no.
    """
    to_resolve = landing_urls[:MAX_PDF_RESOLUTIONS]
    if len(landing_urls) > MAX_PDF_RESOLUTIONS:
        log.warning(
            f"[SEBI] landing details resolution capped at {MAX_PDF_RESOLUTIONS} of "
            f"{len(landing_urls)} records; remaining records keep the landing-page URL"
        )

    concurrency = PDF_RESOLVE_CONCURRENCY if via_proxy else min(PDF_RESOLVE_CONCURRENCY, 6)
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(url: str) -> dict:
        async with semaphore:
            if not via_proxy:
                await asyncio.sleep(0.05)
            return await _resolve_landing_details(client, url, via_proxy)

    resolved = await asyncio.gather(*[_bounded(url) for url in to_resolve])
    resolved = list(resolved)
    
    for url in landing_urls[MAX_PDF_RESOLUTIONS:]:
        resolved.append({"download_url": url, "circular_no": None})
        
    return resolved


async def _resolve_missing_departments_from_pdf_batch(
    client: httpx.AsyncClient,
    rows: list[dict],
    landing_details: list[dict],
    url_to_department: dict[str, str],
    department_label: Optional[str],
    via_proxy: bool,
) -> dict[str, tuple[str, dict, str]]:
    """
    For records where circular_no, Department, and Abbreviation remain unresolved,
    fetch the PDF or HTML in memory temporarily, inspect text, extract the circular number,
    and resolve the department via the canonical department resolver.

    Guarantees:
      - Only runs for records that actually need it (never fetches for already resolved records).
      - Never writes PDFs or HTML to disk.
      - Concurrency bounded by FALLBACK_CONCURRENCY.
    """
    candidates = []
    for row, details in zip(rows, landing_details):
        circular_no = details.get("circular_no")
        landing_url = row.get("url", "")
        id_match = _LANDING_ID_RE.search(landing_url)
        if id_match and id_match.group(1) in KNOWN_CIRCULAR_OVERRIDES:
            continue

        record_dept = url_to_department.get(landing_url, department_label)
        dept_info = assign_department(circular_no, fallback_department=record_dept)

        # Check if fallback is needed: if circular_no is missing OR lacks department letters OR department/abbreviation is unresolved
        if (
            not circular_no
            or not re.search(r"[a-zA-Z]", circular_no)
            or not (dept_info.get("department") or dept_info.get("Department"))
            or not (dept_info.get("abbreviation") or dept_info.get("Abbreviation"))
        ):
            download_url = details.get("download_url") or landing_url
            if download_url and download_url.startswith("http"):
                is_pdf = (
                    download_url.lower().endswith(".pdf")
                    or "/attachdocs/" in download_url
                    or (download_url != landing_url and not download_url.lower().endswith(".html"))
                )
                candidates.append((landing_url, download_url, is_pdf))

    if not candidates:
        return {}

    log.info(
        f"[SEBI Fallback] Invoking fallback extraction for {len(candidates)} records with missing circular_no / department"
    )

    semaphore = asyncio.Semaphore(FALLBACK_CONCURRENCY)

    async def _resolve_one(landing_url: str, doc_url: str, is_pdf: bool):
        async with semaphore:
            extracted_no, dept_name, dept_abbrev = await extract_details_from_url(
                client, doc_url, is_pdf=is_pdf, via_proxy=via_proxy
            )
            dept_info = None
            if extracted_no:
                dept_info = assign_department(extracted_no)
            if not dept_info or not (dept_info.get("department") or dept_info.get("Department")):
                if dept_name:
                    dept_info = {
                        "department": dept_name,
                        "Department": dept_name,
                        "abbreviation": dept_abbrev,
                        "Abbreviation": dept_abbrev,
                    }

            if dept_info and (dept_info.get("department") or dept_info.get("Department")):
                source = "pdf_first_page" if is_pdf else "html_body"
                return landing_url, extracted_no, dept_info, source
            elif extracted_no:
                source = "pdf_first_page" if is_pdf else "html_body"
                return landing_url, extracted_no, {
                    "department": None,
                    "Department": None,
                    "abbreviation": None,
                    "Abbreviation": None,
                }, source
            return landing_url, None, None, None

    results = await asyncio.gather(
        *(_resolve_one(landing_url, doc_url, is_pdf) for landing_url, doc_url, is_pdf in candidates)
    )

    fallback_map: dict[str, tuple[str, dict, str]] = {}
    for landing_url, extracted_no, dept_info, source in results:
        if extracted_no or (dept_info and (dept_info.get("department") or dept_info.get("Department"))):
            fallback_map[landing_url] = (
                extracted_no,
                dept_info or {
                    "department": None,
                    "Department": None,
                    "abbreviation": None,
                    "Abbreviation": None,
                },
                source,
            )

    return fallback_map


# ---------------------------------------------------------------------------
# Shared scrape flow
# ---------------------------------------------------------------------------

def _resolve_sebi_filter_name_to_id(
    raw_value: str,
    primary_map: dict[str, str],
    fallback_map: dict[str, str] | None,
    field_name: str,
) -> str:
    """Map a user-facing SEBI filter name to the numeric upstream id."""
    if not raw_value or raw_value.strip() == "":
        return "-1" if field_name == "department" else "-1"

    if raw_value in primary_map:
        return primary_map[raw_value]

    if fallback_map is not None and raw_value in fallback_map:
        return fallback_map[raw_value]

    raise ValueError(
        f"Invalid SEBI {field_name} filter '{raw_value}'. Use GET /sebi/info for valid values."
    )


async def _scrape(listing: dict, req) -> dict:
    """
    Scrape a SEBI listing (Circulars or Master Circulars) for a date range.

    Upstream pagination (25 rows/page) is walked automatically and merged,
    capped at MAX_PAGES as a safety limit.

    Results are cached in-process:
      - to_date >= today  → 10-minute TTL (new items may be published later
                            the same day)
      - to_date < today   → 7-day TTL (past listings are immutable)
    """
    start_date = _parse_input_date(req.from_date)
    end_date = _parse_input_date(req.to_date)
    if start_date > end_date:
        raise ValueError("from_date must not be after to_date")

    search = (getattr(req, "search", "") or "").strip() if listing["include_search"] else ""
    department = (req.department or "").strip()
    intermediary = (
        (getattr(req, "intermediary", "") or "").strip() if listing["include_intermediary"] else ""
    )
    via_proxy = getattr(req, "via_proxy", False)

    department_id = _resolve_sebi_filter_name_to_id(
        department,
        MASTER_CIRCULAR_DEPARTMENT_NAMES_TO_ID
        if listing is _MASTER_CIRCULAR
        else CIRCULAR_DEPARTMENT_NAMES_TO_ID,
        None,
        "department",
    )
    intermediary_id = _resolve_sebi_filter_name_to_id(
        intermediary,
        INTERMEDIARY_NAMES_TO_ID,
        None,
        "intermediary",
    ) if listing["include_intermediary"] else ""

    from_str = start_date.strftime("%d-%m-%Y")
    to_str = end_date.strftime("%d-%m-%Y")

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    cache_key = TTLCache.make_key(
        "sebi", listing["result_type"], search, from_str, to_str, department, intermediary,
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        log.info(
            f"[SEBI] cache hit: type={listing['result_type']!r} "
            f"{from_str}..{to_str} count={cached['count']}"
        )
        return cached

    log.info(
        f"[SEBI] cache miss: type={listing['result_type']!r} "
        f"{from_str}..{to_str} — fetching from upstream"
    )

    # ------------------------------------------------------------------
    # Upstream fetch (paginated)
    # ------------------------------------------------------------------
    pages_fetched = 0
    async with httpx.AsyncClient() as client:
        rows, total = await _fetch_page(
            client, listing, search, from_str, to_str, department_id, intermediary_id, 0, via_proxy
        )
        all_rows: list[dict] = list(rows)
        pages_fetched += 1

        total_pages = 1
        if total is not None:
            computed_pages = -(-total // PAGE_SIZE)  # ceil division
            if computed_pages > MAX_PAGES:
                log.warning(
                    f"[SEBI] result set truncated: type={listing['result_type']!r} "
                    f"total={total} capped at {MAX_PAGES * PAGE_SIZE} records"
                )
            total_pages = min(computed_pages, MAX_PAGES)

        for page_index in range(1, total_pages):
            try:
                rows, _ = await _fetch_page(
                    client, listing, search, from_str, to_str, department_id, intermediary_id,
                    page_index, via_proxy,
                )
                all_rows.extend(rows)
                pages_fetched += 1
            except Exception:
                log.exception(
                    f"[SEBI] page fetch failed: type={listing['result_type']!r} "
                    f"page_index={page_index}; skipping"
                )

        # --------------------------------------------------------------
        # Resolve each landing page to its real PDF url and Circular No
        # (falls back to the landing page URL per-record on failure — never
        # raises), and, if requested, each record's real department —
        # concurrently, since both are independent upstream calls.
        # --------------------------------------------------------------
        resolved_landing_details = await _resolve_landing_details_batch(
            client, [row["url"] for row in all_rows], via_proxy
        )
        url_to_department: dict[str, str] = {}

        # --------------------------------------------------------------
        # PDF Fallback for records with missing circular_no & department
        # Only triggered if Department, Abbreviation, and circular_no are null
        # --------------------------------------------------------------
        department_label = (
            department or listing["dept_map"].get("-1", "")
        ) if listing["annotate_department"] else None

        pdf_fallback_map = await _resolve_missing_departments_from_pdf_batch(
            client,
            all_rows,
            resolved_landing_details,
            url_to_department,
            department_label,
            via_proxy,
        )

    # ------------------------------------------------------------------
    # Normalise records
    # ------------------------------------------------------------------
    records: list[dict] = []
    for row, details in zip(all_rows, resolved_landing_details):
        landing_url = row["url"]
        id_match = _LANDING_ID_RE.search(landing_url or "")
        circular_no = details["circular_no"]
        record_dept = (
            url_to_department.get(row["url"], department_label)
            if listing["annotate_department"]
            else None
        )
        dept_info = assign_department(circular_no, fallback_department=record_dept)

        # Check if fallback resolved this record
        if landing_url in pdf_fallback_map:
            extracted_no, fallback_dept_info, source = pdf_fallback_map[landing_url]
            if fallback_dept_info.get("Department") or fallback_dept_info.get("department"):
                dept_info = fallback_dept_info
                log.info(
                    f"[SEBI] Department resolved from {source}: {dept_info.get('Department')} ({dept_info.get('Abbreviation')}) for {landing_url}"
                )
            if extracted_no and (not circular_no or not re.search(r"[a-zA-Z]", circular_no)):
                circular_no = extracted_no

        # Check known manual overrides for circular_no (supersedes any noisy extractions)
        if id_match and id_match.group(1) in KNOWN_CIRCULAR_OVERRIDES:
            circular_no = KNOWN_CIRCULAR_OVERRIDES[id_match.group(1)]
        elif not circular_no and details.get("download_url"):
            for key, val in KNOWN_CIRCULAR_OVERRIDES.items():
                if key in details["download_url"]:
                    circular_no = val
                    break

        # Check known department overrides or assign from circular_no
        if id_match and id_match.group(1) in KNOWN_DEPARTMENT_OVERRIDES:
            known_dept, known_abbr = KNOWN_DEPARTMENT_OVERRIDES[id_match.group(1)]
            dept_info = {"department": known_dept, "abbreviation": known_abbr}
        elif circular_no:
            dept_info = assign_department(circular_no, fallback_department=record_dept)

        record = {
            "date": row["date"],
            "title": row["title"],
            "circular_no": circular_no,
            "circular_id": id_match.group(1) if id_match else None,
            "html_url": landing_url,
            "download_url": details["download_url"],
            "type": listing["result_type"],
            "department": dept_info.get("department") or dept_info.get("Department"),
            "abbreviation": dept_info.get("abbreviation") or dept_info.get("Abbreviation"),
        }

        records.append(record)

    filters_applied = {
        "from_date": from_str,
        "to_date": to_str,
        "department": department or listing["dept_map"].get("-1", ""),
    }
    if listing["include_search"]:
        filters_applied["search"] = search
    if listing["include_intermediary"]:
        filters_applied["intermediary"] = intermediary or INTERMEDIARIES.get("-1", "")

    result = {
        "source": "sebi",
        "url": listing["page_url"],
        "filters_applied": filters_applied,
        "pages_fetched": pages_fetched,
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
        f"[SEBI] cached result: type={listing['result_type']!r} {from_str}..{to_str} "
        f"ttl={ttl}s ({'short' if end_date >= today else 'long'})"
    )

    return result


# ---------------------------------------------------------------------------
# Public scraper functions
# ---------------------------------------------------------------------------

async def scrape_sebi_circulars(req) -> dict:
    """Scrape SEBI Circulars for a date range from the Legal > Circulars listing."""
    return await _scrape(_CIRCULAR, req)


async def scrape_sebi_master_circulars(req) -> dict:
    """Scrape SEBI Master Circulars for a date range from the Legal > Master Circulars listing."""
    return await _scrape(_MASTER_CIRCULAR, req)
