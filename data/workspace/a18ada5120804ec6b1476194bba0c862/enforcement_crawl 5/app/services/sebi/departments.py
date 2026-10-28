"""
app/services/sebi/departments.py
=================================
Canonical department map and circular-number-based department detection for SEBI.

HOW DETECTION WORKS
-------------------
A SEBI circular number such as "MRD/DoP/CIR/2026/123" starts with a department
abbreviation followed by a separator (/, -, _, space, or .).
Detection rules:
  1.  Normalise the circular number to UPPERCASE and strip whitespace.
  2.  Try every canonical abbreviation, longest first, against the leading token
      (= the text up to the first separator character after the abbreviation).
  3.  A match is only accepted if the abbreviation is the *entire* leading
      token, not a substring inside a longer word.
  4.  Sub-divisions (MRD_POD, CFD-RAC, ITD-2, ...) collapse to their parent
      canonical code automatically because we match only the first segment.
  5.  Unknown abbreviations return (None, None) and are logged.
  6.  Possible aliases (differ by <=1 Levenshtein edit from a known code) are
      flagged but never auto-applied.

ALIASES
-------
`DEPARTMENT_ALIASES` maps non-canonical or historical codes -> canonical code.
Add new aliases here after manual confirmation; never auto-generate them.

Usage
-----
    from app.services.sebi.departments import assign_department

    dept_info = assign_department(circular_no)
    record["department"]   = dept_info["department"]
    record["abbreviation"] = dept_info["abbreviation"]
"""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1.  Canonical department map  (abbreviation -> full name)
# ---------------------------------------------------------------------------

DEPARTMENT_MAP: dict[str, str] = {
    "MRD":   "Market Regulation Department",
    "CFD":   "Corporation Finance Department",
    "CFID":  "Corporation Finance Investigation Department",
    "CDMRD": "Commodity Derivatives Market Regulation Department",
    "IMD":   "Investment Management Department",
    "AFD":   "Alternative Investment Fund and Foreign Portfolio Investors Department",
    "DDHS":  "Department of Debt and Hybrid Securities",
    "DEPA":  "Department of Economic & Policy Analysis",
    "EAD":   "Enquiries and Adjudication Department",
    "QJC":   "Quasi-Judicial Cell",
    "MIRSD": "Market Intermediaries Regulation and Supervision Department",
    "LAD":   "Legal Affairs Department",
    "EFD":   "Enforcement Department",
    "PSD":   "Prosecution and Settlement Department",
    "SSLD":  "SAT-SC Department",
    "RRD":   "Recovery and Refund Department",
    "OIAE":  "Office of Investor Assistance and Education",
    "GSD":   "General Services Department",
    "ITD":   "Information Technology Department",
    "IVD":   "Investigations Department",
    "ISD":   "Integrated Surveillance Department",
    "OCH":   "Office of Chairman",
    "VD":    "Vigilance Department",
    "HRD":   "Human Resources Department",
    "IID":   "Internal Inspection Department",
    "PACL":  "PACL Committee",
    "NISM":  "National Institute of Securities Markets",
    "OIA":   "Office of International Affairs",
}

# ---------------------------------------------------------------------------
# 2.  Alias table  (non-canonical / historical -> canonical code)
#     Only add after human confirmation -- never auto-generated.
# ---------------------------------------------------------------------------

DEPARTMENT_ALIASES: dict[str, Optional[str]] = {
    # Non-department prefixes to skip
    "HO": None,
    "SEBI": None,
    "CIR": None,
    "CIRCULAR": None,
    # Historical / division mappings
    "IDD": "IVD",       # Investigations Department
    "FPIC": "AFD",      # Foreign Portfolio Investors Cell / Division -> AFD (or IMD)
    "FITTC": "AFD",     # Foreign Institutional Investors -> AFD
    "FII": "AFD",       # Foreign Institutional Investors -> AFD
    "SMD": "MRD",       # Secondary Market Department -> Market Regulation Department
    "SMDRP": "MRD",     # Secondary Market Department - Regulation and Policy -> MRD
    "SMDRPD": "MRD",    # Secondary Market Depository Research and Policy Division -> MRD
    "CBM": "MRD",       # Corporate Bond Market -> Market Regulation Department
    "BOND": "MRD",      # Corporate Bond Market -> Market Regulation Department
    "DNPD": "MRD",      # Derivatives and New Products Department -> MRD
    "DMS": "MRD",       # Division of Market Supervision -> MRD
    "DSA": "MRD",       # Division of SROs and Administration -> MRD
    "DOP": "MRD",       # Division of Policy -> MRD
    "DP": "MRD",        # Division of Policy -> MRD
    "SE": "MRD",        # Stock Exchanges Division -> MRD
    "SU": "MRD",        # Surveillance Unit -> MRD
    "PMD": "CFD",       # Primary Market Department -> Corporation Finance Department
    "DIL": "CFD",       # Division of Issues and Listing -> CFD
    "RMB": "CFD",       # Registered Merchant Bankers -> CFD
    "DIP": "CFD",       # Disclosure and Investor Protection -> CFD
    "MFD": "IMD",       # Mutual Funds Department -> Investment Management Department
    "RPM": "IMD",       # Registered Portfolio Managers -> IMD
    "DF1": "IMD",       # Division of Funds 1 -> IMD
    "DF2": "IMD",       # Division of Funds 2 -> IMD
    "CUST": "IMD",      # Custodians -> IMD
    "RTI": "MIRSD",     # Registrars to an Issue -> MIRSD
    "DPS": "MIRSD",     # Division of Policy and Supervision -> MIRSD
    "D&CC": "MIRSD",    # Depositories and Custodians Cell -> MIRSD
    "DCC": "MIRSD",     # Depositories and Custodians Cell -> MIRSD
    "IES": "ISD",       # Investigations Enforcement and Surveillance -> ISD
    "IESD": "OIAE",     # Investor Education and Surveillance Department -> OIAE
    "MAPIN": "MIRSD",   # Market Participants Identification Number -> MIRSD
    "SBD": "MIRSD",     # Stock Broker Division -> MIRSD
    "RTA": "MIRSD",     # Registrars and Transfer Agents -> MIRSD
    "RTAMB": "MIRSD",   # RTA & Merchant Bankers -> MIRSD
    "DRA": "DEPA",      # Department of Regulatory Affairs -> DEPA
    "DRMP": "CDMRD",    # Division of Risk Management & Products -> CDMRD
    "DMP": "CDMRD",     # Division of Market Policy -> CDMRD
    # Additional confirmed historical/division aliases
    "FPI&C": "AFD",     # Foreign Portfolio Investors & Custodians -> AFD
    "FPI": "AFD",       # Foreign Portfolio Investors -> AFD
    "IFSC": "MRD",      # International Financial Services Centres -> MRD
    "DRMNP": "MRD",     # Division of Risk Management & New Products -> MRD
    "LGL": "LAD",       # Legal Division / Affairs -> LAD
    "RRTI": "CFD",      # Registrars to an Issue -> CFD
    "RUW": "CFD",       # Underwriters -> CFD
    "BTI": "CFD",       # Bankers to an Issue -> CFD
    "DT": "CFD",        # Debenture Trustees -> CFD
    "CRD": "CFD",       # Corporate Restructuring Division -> CFD
    "PMIMD": "CFD",     # Primary Market Department -> CFD
    "VCF": "IMD",       # Venture Capital Funds -> IMD
}

# ---------------------------------------------------------------------------
# Internal helpers & reverse map
# ---------------------------------------------------------------------------

# Separator characters that delimit tokens in circular numbers
_TOKEN_SPLIT_RE = re.compile(r"[/_.\s\-():,]+")

# All known abbreviations (canonical + aliases), sorted longest-first
ALL_KNOWN_ABBREVS: list[str] = sorted(
    list(DEPARTMENT_MAP.keys()) + [k for k, v in DEPARTMENT_ALIASES.items() if v is not None],
    key=len,
    reverse=True,
)

# Reverse mapping for full department names (case-insensitive & punctuation-normalised)
REVERSE_DEPARTMENT_MAP: dict[str, tuple[str, str]] = {}
for code, name in DEPARTMENT_MAP.items():
    clean_k = re.sub(r"[^a-zA-Z0-9]+", " ", name).strip().lower()
    REVERSE_DEPARTMENT_MAP[clean_k] = (name, code)

# Common aliases / variations from SEBI dropdowns, listings, and HTML headers
REVERSE_DEPARTMENT_MAP["department economic and policy analysis"] = (
    DEPARTMENT_MAP["DEPA"],
    "DEPA",
)
REVERSE_DEPARTMENT_MAP["enforcement department 1"] = (
    DEPARTMENT_MAP["EFD"],
    "EFD",
)
REVERSE_DEPARTMENT_MAP["enforcement department 2"] = (
    DEPARTMENT_MAP["EFD"],
    "EFD",
)
REVERSE_DEPARTMENT_MAP["secondary market department"] = (
    DEPARTMENT_MAP["MRD"],
    "MRD",
)
REVERSE_DEPARTMENT_MAP["secondary market department regulation and policy"] = (
    DEPARTMENT_MAP["MRD"],
    "MRD",
)
REVERSE_DEPARTMENT_MAP["primary market department"] = (
    DEPARTMENT_MAP["CFD"],
    "CFD",
)
REVERSE_DEPARTMENT_MAP["mutual funds department"] = (
    DEPARTMENT_MAP["IMD"],
    "IMD",
)
REVERSE_DEPARTMENT_MAP["derivatives and new products department"] = (
    DEPARTMENT_MAP["MRD"],
    "MRD",
)
REVERSE_DEPARTMENT_MAP["derivatives new products department"] = (
    DEPARTMENT_MAP["MRD"],
    "MRD",
)
REVERSE_DEPARTMENT_MAP["investor education and surveillance department"] = (
    DEPARTMENT_MAP["OIAE"],
    "OIAE",
)
REVERSE_DEPARTMENT_MAP["department of regulatory affairs"] = (
    DEPARTMENT_MAP["DEPA"],
    "DEPA",
)
REVERSE_DEPARTMENT_MAP["imd department"] = (
    DEPARTMENT_MAP["IMD"],
    "IMD",
)


def _levenshtein(a: str, b: str) -> int:
    """Simple O(m*n) Levenshtein distance (short strings only)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# 3.  Public API
# ---------------------------------------------------------------------------

def detect_department(circular_no: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Detect SEBI department from a circular number.

    Returns (department_name, canonical_abbreviation) or (None, None) if unknown.

    Handles standard, prefix-based, and multi-segment circular numbers (e.g.
    'HO/47/16/13(4)2026-MRD-POD1/I/14266/2026', '38/12/11(5)2026-MIRSD-POD/...',
    'MRD/DoP/CIR/2026/123', 'SEBI/HO/CFD/...', '(201)2026-DDHS-POD1...',
    'SMDRP/Policy/Cir-25/2001', 'MFD/CIR No. 22 / 2311 /03', 'SEBI/DNPD/Cir- 52 /2010').

    Sub-divisions (e.g. MRD_POD-2, CFD/SEC1, SMDRP) resolve to the parent canonical code.
    """
    if not circular_no or not str(circular_no).strip():
        return None, None

    normalised = str(circular_no).strip().upper()
    tokens = [t for t in _TOKEN_SPLIT_RE.split(normalised) if t]

    if not tokens:
        return None, None

    # Step 1: Scan tokens across the circular number
    for token in tokens:
        # Check alias table directly
        if token in DEPARTMENT_ALIASES:
            canonical = DEPARTMENT_ALIASES[token]
            if canonical:
                return DEPARTMENT_MAP.get(canonical), canonical
            continue

        # Direct match in canonical map
        if token in DEPARTMENT_MAP:
            return DEPARTMENT_MAP[token], token

        # Longest-prefix match on token (e.g. "MRD-POD1" -> "MRD", "MIRSD1" -> "MIRSD", "SMDRP" -> "MRD")
        for abbrev in ALL_KNOWN_ABBREVS:
            if token.startswith(abbrev):
                remainder = token[len(abbrev):]
                if not remainder or re.match(r"^[0-9]+$", remainder) or re.match(
                    r"^(?:POD|RAC|DIL|SEC|TPD|FATF|DOP|RTAMB|MMC|FPICELL|CIMGI|POLICY|DSA|DP|SU|SE|CUST|DRMP|DMP|DF1|DF2|CMD|DBA)[0-9A-Z]*$",
                    remainder,
                ):
                    canon = DEPARTMENT_ALIASES.get(abbrev, abbrev)
                    return DEPARTMENT_MAP.get(canon, canon), canon

    # Step 2: Unknown -- check for near-misses on uppercase tokens (length >= 3, skipping stopwords)
    first_token = tokens[0] if tokens else normalised
    _STOPWORDS = {"IS", "TO", "OF", "IN", "ON", "AT", "BY", "FOR", "THE", "AND", "OR", "AS", "AN"}
    if len(first_token) >= 3 and first_token not in _STOPWORDS and first_token.isupper():
        possible_aliases: list[str] = [
            abbrev for abbrev in DEPARTMENT_MAP if _levenshtein(first_token, abbrev) == 1
        ]
        if possible_aliases:
            log.warning(
                f"POSSIBLE_ALIAS: circular_no={circular_no!r} "
                f"token={first_token!r} possible_canonical={possible_aliases}"
            )
        else:
            log.warning(
                f"UNKNOWN_DEPARTMENT_ABBREVIATION: {circular_no!r} (tokens={tokens})"
            )

    return None, None


def assign_department(
    circular_no: Optional[str],
    fallback_department: Optional[str] = None,
) -> dict:
    """
    Given a circular number and optional fallback department name, return a dict
    with Department and Abbreviation keys.

    Examples:
        assign_department("MRD/DoP/CIR/2026/123")
        -> {"Department": "Market Regulation Department", "Abbreviation": "MRD"}

        assign_department("HO/47/16/13(4)2026-MRD-POD1/I/14266/2026")
        -> {"Department": "Market Regulation Department", "Abbreviation": "MRD"}

        assign_department(None, fallback_department="Secondary Market Department")
        -> {"Department": "Market Regulation Department", "Abbreviation": "MRD"}

        assign_department(None, fallback_department="Legal Affairs Department")
        -> {"Department": "Legal Affairs Department", "Abbreviation": "LAD"}

        assign_department(None, fallback_department="All Department")
        -> {"Department": None, "Abbreviation": None}
    """
    dept, abbrev = detect_department(circular_no)

    if not dept and fallback_department:
        clean = re.sub(r"[^a-zA-Z0-9]+", " ", str(fallback_department)).strip().lower()
        if clean in REVERSE_DEPARTMENT_MAP:
            dept, abbrev = REVERSE_DEPARTMENT_MAP[clean]
        else:
            # Precision substring match: require ≥ 2 meaningful content words in common.
            # Content words are non-stopword tokens of length ≥ 4 chars.
            # This avoids false positives from generic terms like "department" or "office"
            # matching the first canonical entry that happens to contain that word.
            _STOPWORDS = frozenset({
                "of", "and", "or", "the", "in", "at", "by", "for",
                "an", "a", "to", "from", "all", "any", "new",
            })
            query_content = [
                w for w in clean.split()
                if w not in _STOPWORDS and len(w) >= 4
            ]
            if len(query_content) >= 2:
                query_set = set(query_content)
                best_match_name: Optional[str] = None
                best_match_code: Optional[str] = None
                best_overlap = 0
                for key, (full_name, code) in REVERSE_DEPARTMENT_MAP.items():
                    key_content = set(
                        w for w in key.split()
                        if w not in _STOPWORDS and len(w) >= 4
                    )
                    overlap = len(query_set & key_content)
                    if overlap >= 2 and overlap > best_overlap:
                        best_match_name = full_name
                        best_match_code = code
                        best_overlap = overlap
                if best_match_name:
                    dept, abbrev = best_match_name, best_match_code

    return {
        "department": dept,
        "Department": dept,
        "abbreviation": abbrev,
        "Abbreviation": abbrev,
    }
