"""
tests/test_departments.py
=========================
Comprehensive regression tests for SEBI department detection and assignment logic.

Covers:
  - TEST 1:  detect_department with standard LAD/RRD circular numbers
  - TEST 2:  detect_department with various HO-prefixed modern circular numbers
  - TEST 3:  assign_department with LAD/RRD circular_no prefix (data-driven)
  - TEST 4:  assign_department with SEBI dropdown dept names as fallback (all 10 current)
  - TEST 5:  assign_department with historical/alias dept names as fallback
  - TEST 6:  assign_department fallback for 'All Department' returns None (not a false match)
  - TEST 7:  assign_department fallback with single generic words returns None (no false positives)
  - TEST 8:  assign_department fallback with LAD/RRD full names returns correct dept
  - TEST 9:  assign_department with numeric-only circular_no falls through to fallback
  - TEST 10: detect_department for all DEPARTMENT_MAP canonical codes
  - TEST 11: detect_department for DEPARTMENT_ALIASES -> resolves to canonical
  - TEST 12: detect_department for HO/XX/<NN> modern format (subdivision stripping)
  - TEST 13: No false positive substring matches: 'department', 'office', 'enforcement' alone
  - TEST 14: Positive substring matches: 2+ meaningful content words -> correct dept
  - TEST 15: REVERSE_DEPARTMENT_MAP has no duplicate canonical codes for common SEBI names
"""

import re
import unittest

from app.services.sebi.departments import (
    DEPARTMENT_MAP,
    DEPARTMENT_ALIASES,
    REVERSE_DEPARTMENT_MAP,
    assign_department,
    detect_department,
)


class TestDetectDepartment(unittest.TestCase):
    """TEST 1-4, 10-12: detect_department core behaviour."""

    # TEST 1: LAD and RRD circular number prefixes
    def test_lad_standard_prefix(self):
        dept, abbr = detect_department("LAD/NRO/GN/2016/001")
        self.assertEqual(dept, "Legal Affairs Department")
        self.assertEqual(abbr, "LAD")

    def test_lad_sebi_ho_prefix(self):
        dept, abbr = detect_department("SEBI/HO/LAD/NRO/GN/2019/30")
        self.assertEqual(dept, "Legal Affairs Department")
        self.assertEqual(abbr, "LAD")

    def test_lad_sebi_ho_latest(self):
        dept, abbr = detect_department("SEBI/HO/LAD/NRO/GN/2023/159")
        self.assertEqual(dept, "Legal Affairs Department")
        self.assertEqual(abbr, "LAD")

    def test_lad_cir_prefix(self):
        dept, abbr = detect_department("CIR/LAD/1/2016")
        self.assertEqual(dept, "Legal Affairs Department")
        self.assertEqual(abbr, "LAD")

    def test_lad_ho_prefix_short(self):
        dept, abbr = detect_department("HO/LAD/NRO/GN/2023/01")
        self.assertEqual(dept, "Legal Affairs Department")
        self.assertEqual(abbr, "LAD")

    def test_rrd_standard_prefix(self):
        dept, abbr = detect_department("RRD/CIR/1/2020")
        self.assertEqual(dept, "Recovery and Refund Department")
        self.assertEqual(abbr, "RRD")

    def test_rrd_sebi_ho_prefix(self):
        dept, abbr = detect_department("SEBI/HO/RRD/CIR/P/2023/159")
        self.assertEqual(dept, "Recovery and Refund Department")
        self.assertEqual(abbr, "RRD")

    def test_rrd_sebi_ho_cir(self):
        dept, abbr = detect_department("SEBI/HO/RRD/CIR/2020/001")
        self.assertEqual(dept, "Recovery and Refund Department")
        self.assertEqual(abbr, "RRD")

    # TEST 2: Modern HO-prefixed circular numbers (subdivisions)
    def test_ho_mrd_pod(self):
        dept, abbr = detect_department("HO/47/11/11(1)2026-MRD-POD3/I/19839/2026")
        self.assertEqual(dept, "Market Regulation Department")
        self.assertEqual(abbr, "MRD")

    def test_ho_mirsd_pod(self):
        dept, abbr = detect_department("38/12/11(5)2026-MIRSD-POD/I/18796/2026")
        self.assertEqual(dept, "Market Intermediaries Regulation and Supervision Department")
        self.assertEqual(abbr, "MIRSD")

    def test_sebi_ho_cfd_div(self):
        dept, abbr = detect_department("SEBI/HO/CFD/DIL2/CIR/P/2020/243")
        self.assertEqual(dept, "Corporation Finance Department")
        self.assertEqual(abbr, "CFD")

    def test_sebi_ho_imd(self):
        dept, abbr = detect_department("SEBI/HO/IMD/IMD-1/CIR/P/2019/67")
        self.assertEqual(dept, "Investment Management Department")
        self.assertEqual(abbr, "IMD")

    def test_sebi_ho_itd(self):
        dept, abbr = detect_department("HO/(449)2026-ITD-5_DIV1/I/19448/2026")
        self.assertEqual(dept, "Information Technology Department")
        self.assertEqual(abbr, "ITD")

    # TEST 10: All canonical codes detect correctly
    def test_all_canonical_codes_detect(self):
        for code, expected_name in DEPARTMENT_MAP.items():
            with self.subTest(code=code):
                dept, abbr = detect_department(f"SEBI/HO/{code}/CIR/1/2020/1")
                self.assertEqual(dept, expected_name, f"Code {code!r} did not resolve")
                self.assertEqual(abbr, code)

    # TEST 11: DEPARTMENT_ALIASES resolve to canonical
    def test_alias_smdrp_resolves_to_mrd(self):
        dept, abbr = detect_department("SMDRP/Policy/Cir-25/2001")
        self.assertEqual(dept, "Market Regulation Department")
        self.assertEqual(abbr, "MRD")

    def test_alias_mfd_resolves_to_imd(self):
        dept, abbr = detect_department("MFD/CIR No. 22 / 2311 /03")
        self.assertEqual(dept, "Investment Management Department")
        self.assertEqual(abbr, "IMD")

    def test_alias_smd_resolves_to_mrd(self):
        dept, abbr = detect_department("SMD/POLICY/Cir - 3 /03")
        self.assertEqual(dept, "Market Regulation Department")
        self.assertEqual(abbr, "MRD")

    def test_alias_idd_resolves_to_ivd(self):
        dept, abbr = detect_department("IDD/CIR/2001/1")
        self.assertEqual(dept, "Investigations Department")
        self.assertEqual(abbr, "IVD")

    def test_alias_fii_resolves_to_afd(self):
        dept, abbr = detect_department("FII/CIR/2010/1")
        self.assertEqual(
            dept,
            "Alternative Investment Fund and Foreign Portfolio Investors Department",
        )
        self.assertEqual(abbr, "AFD")

    def test_alias_pmd_resolves_to_cfd(self):
        dept, abbr = detect_department("PMD/CIR/2005/10")
        self.assertEqual(dept, "Corporation Finance Department")
        self.assertEqual(abbr, "CFD")

    # TEST 12: HO / SEBI / CIR prefix tokens are skipped, not matched
    def test_ho_prefix_skipped(self):
        """HO token should be skipped (alias -> None), fall through to next token."""
        dept, abbr = detect_department("HO/LAD/NRO/GN/2019/30")
        self.assertEqual(abbr, "LAD")

    def test_sebi_prefix_skipped(self):
        """SEBI token should be skipped, fall through to next token."""
        dept, abbr = detect_department("SEBI/MRD/DoP/CIR/2019/10")
        self.assertEqual(abbr, "MRD")

    def test_none_returns_none(self):
        dept, abbr = detect_department(None)
        self.assertIsNone(dept)
        self.assertIsNone(abbr)

    def test_empty_string_returns_none(self):
        dept, abbr = detect_department("")
        self.assertIsNone(dept)
        self.assertIsNone(abbr)

    def test_whitespace_only_returns_none(self):
        dept, abbr = detect_department("   ")
        self.assertIsNone(dept)
        self.assertIsNone(abbr)


class TestAssignDepartmentCircularNoPath(unittest.TestCase):
    """TEST 3: assign_department when circular_no has the dept prefix."""

    def test_lad_via_circular_no(self):
        r = assign_department("SEBI/HO/LAD/NRO/GN/2019/30")
        self.assertEqual(r["department"], "Legal Affairs Department")
        self.assertEqual(r["abbreviation"], "LAD")

    def test_rrd_via_circular_no(self):
        r = assign_department("SEBI/HO/RRD/CIR/P/2023/159")
        self.assertEqual(r["department"], "Recovery and Refund Department")
        self.assertEqual(r["abbreviation"], "RRD")

    def test_mrd_via_circular_no(self):
        r = assign_department("MRD/DoP/CIR/2026/123")
        self.assertEqual(r["department"], "Market Regulation Department")
        self.assertEqual(r["abbreviation"], "MRD")

    def test_return_keys_include_both_cases(self):
        """Both 'department'/'Department' and 'abbreviation'/'Abbreviation' must be present."""
        r = assign_department("MRD/CIR/1/2020")
        self.assertIn("department", r)
        self.assertIn("Department", r)
        self.assertIn("abbreviation", r)
        self.assertIn("Abbreviation", r)
        self.assertEqual(r["department"], r["Department"])
        self.assertEqual(r["abbreviation"], r["Abbreviation"])

    # TEST 9: numeric-only circular_no falls through to fallback
    def test_numeric_only_circular_no_uses_fallback(self):
        """Numeric-only circular_no cannot detect dept; fallback_department is used instead."""
        r = assign_department("127", fallback_department="Legal Affairs Department")
        self.assertEqual(r["department"], "Legal Affairs Department")
        self.assertEqual(r["abbreviation"], "LAD")

    def test_numeric_only_no_fallback_returns_none(self):
        r = assign_department("127")
        self.assertIsNone(r["department"])
        self.assertIsNone(r["abbreviation"])


class TestAssignDepartmentFallback(unittest.TestCase):
    """TEST 4-8, 13-14: assign_department fallback_department resolution."""

    # TEST 4: All 10 current SEBI Circulars dropdown dept names
    def test_sebi_dropdown_alternative_investment_fund(self):
        r = assign_department(
            None,
            fallback_department=(
                "Alternative Investment Fund and Foreign Portfolio Investors Department"
            ),
        )
        self.assertEqual(r["abbreviation"], "AFD")

    def test_sebi_dropdown_corporation_finance(self):
        r = assign_department(None, fallback_department="Corporation Finance Department")
        self.assertEqual(r["abbreviation"], "CFD")

    def test_sebi_dropdown_department_economic_policy(self):
        """SEBI dropdown omits 'of' and '&' -- must still resolve correctly."""
        r = assign_department(
            None, fallback_department="Department Economic and Policy Analysis"
        )
        self.assertEqual(r["department"], "Department of Economic & Policy Analysis")
        self.assertEqual(r["abbreviation"], "DEPA")

    def test_sebi_dropdown_ddhs(self):
        r = assign_department(
            None, fallback_department="Department of Debt and Hybrid Securities"
        )
        self.assertEqual(r["abbreviation"], "DDHS")

    def test_sebi_dropdown_enforcement_dept_1(self):
        """Enforcement Department - 1 (with ' - 1' suffix) maps to EFD."""
        r = assign_department(None, fallback_department="Enforcement Department - 1")
        self.assertEqual(r["abbreviation"], "EFD")

    def test_sebi_dropdown_information_technology(self):
        r = assign_department(None, fallback_department="Information Technology Department")
        self.assertEqual(r["abbreviation"], "ITD")

    def test_sebi_dropdown_investment_management(self):
        r = assign_department(None, fallback_department="Investment Management Department")
        self.assertEqual(r["abbreviation"], "IMD")

    def test_sebi_dropdown_mirsd(self):
        r = assign_department(
            None,
            fallback_department=(
                "Market Intermediaries Regulation and Supervision Department"
            ),
        )
        self.assertEqual(r["abbreviation"], "MIRSD")

    def test_sebi_dropdown_market_regulation(self):
        r = assign_department(None, fallback_department="Market Regulation Department")
        self.assertEqual(r["abbreviation"], "MRD")

    def test_sebi_dropdown_oiae(self):
        r = assign_department(
            None, fallback_department="Office of Investor Assistance and Education"
        )
        self.assertEqual(r["abbreviation"], "OIAE")

    def test_sebi_dropdown_integrated_surveillance(self):
        """Appears in the Master Circulars dropdown."""
        r = assign_department(None, fallback_department="Integrated Surveillance Department")
        self.assertEqual(r["abbreviation"], "ISD")

    # TEST 5: Historical / alias dept names
    def test_historical_secondary_market_dept(self):
        r = assign_department(None, fallback_department="Secondary Market Department")
        self.assertEqual(r["abbreviation"], "MRD")

    def test_historical_mutual_funds_dept(self):
        r = assign_department(None, fallback_department="Mutual Funds Department")
        self.assertEqual(r["abbreviation"], "IMD")

    def test_historical_primary_market_dept(self):
        r = assign_department(None, fallback_department="Primary Market Department")
        self.assertEqual(r["abbreviation"], "CFD")

    def test_historical_derivatives_new_products(self):
        r = assign_department(
            None, fallback_department="Derivatives and New Products Department"
        )
        self.assertEqual(r["abbreviation"], "MRD")

    # TEST 6: 'All Department' and empty fallbacks return None
    def test_all_department_label_returns_none(self):
        """'All Department' is SEBI's 'no filter' label; must NOT match any department."""
        r = assign_department(None, fallback_department="All Department")
        self.assertIsNone(r["department"])
        self.assertIsNone(r["abbreviation"])

    def test_sebi_dash_all_department_label_returns_none(self):
        """'-- All Department --' variant also must return None."""
        r = assign_department(None, fallback_department="-- All Department --")
        self.assertIsNone(r["department"])
        self.assertIsNone(r["abbreviation"])

    def test_empty_fallback_returns_none(self):
        r = assign_department(None, fallback_department="")
        self.assertIsNone(r["department"])

    def test_none_fallback_returns_none(self):
        r = assign_department(None, fallback_department=None)
        self.assertIsNone(r["department"])

    # TEST 7 / TEST 13: Single generic words must NOT trigger false positives
    def test_single_word_department_no_false_positive(self):
        """'department' alone should NOT match any canonical department."""
        r = assign_department(None, fallback_department="department")
        self.assertIsNone(r["department"])

    def test_single_word_office_no_false_positive(self):
        r = assign_department(None, fallback_department="office")
        self.assertIsNone(r["department"])

    def test_single_word_legal_no_false_positive(self):
        r = assign_department(None, fallback_department="legal")
        self.assertIsNone(r["department"])

    def test_single_word_recovery_no_false_positive(self):
        r = assign_department(None, fallback_department="recovery")
        self.assertIsNone(r["department"])

    def test_single_word_enforcement_no_false_positive(self):
        r = assign_department(None, fallback_department="enforcement")
        self.assertIsNone(r["department"])

    def test_all_word_no_false_positive(self):
        r = assign_department(None, fallback_department="all")
        self.assertIsNone(r["department"])

    # TEST 8: Full LAD / RRD names via fallback_department
    def test_lad_full_name_via_fallback(self):
        r = assign_department(None, fallback_department="Legal Affairs Department")
        self.assertEqual(r["department"], "Legal Affairs Department")
        self.assertEqual(r["abbreviation"], "LAD")

    def test_rrd_full_name_via_fallback(self):
        r = assign_department(None, fallback_department="Recovery and Refund Department")
        self.assertEqual(r["department"], "Recovery and Refund Department")
        self.assertEqual(r["abbreviation"], "RRD")

    def test_lad_uppercase_via_fallback(self):
        """Case-insensitive matching: UPPERCASE should also resolve."""
        r = assign_department(None, fallback_department="LEGAL AFFAIRS DEPARTMENT")
        self.assertEqual(r["department"], "Legal Affairs Department")
        self.assertEqual(r["abbreviation"], "LAD")

    def test_rrd_uppercase_via_fallback(self):
        r = assign_department(None, fallback_department="RECOVERY AND REFUND DEPARTMENT")
        self.assertEqual(r["department"], "Recovery and Refund Department")
        self.assertEqual(r["abbreviation"], "RRD")

    # TEST 14: Precision substring match (>= 2 content words)
    def test_partial_market_regulation_two_words(self):
        """'Market Regulation' (2 content words) should match MRD via word-overlap match."""
        r = assign_department(None, fallback_department="Market Regulation")
        self.assertEqual(r["abbreviation"], "MRD")

    def test_partial_information_technology_two_words(self):
        """'Information Technology' should match ITD."""
        r = assign_department(None, fallback_department="Information Technology")
        self.assertEqual(r["abbreviation"], "ITD")

    def test_partial_legal_affairs_two_words(self):
        """'Legal Affairs' (2 content words) should match LAD."""
        r = assign_department(None, fallback_department="Legal Affairs")
        self.assertEqual(r["abbreviation"], "LAD")


class TestReverseDepartmentMap(unittest.TestCase):
    """TEST 15: REVERSE_DEPARTMENT_MAP integrity checks."""

    def test_all_dept_map_codes_in_reverse_map(self):
        """Every canonical DEPARTMENT_MAP entry must exist in REVERSE_DEPARTMENT_MAP."""
        for code, name in DEPARTMENT_MAP.items():
            clean = re.sub(r"[^a-zA-Z0-9]+", " ", name).strip().lower()
            self.assertIn(
                clean,
                REVERSE_DEPARTMENT_MAP,
                f"Canonical name {name!r} (code={code}) not found in REVERSE_DEPARTMENT_MAP",
            )

    def test_no_none_values_in_reverse_map(self):
        """Every entry in REVERSE_DEPARTMENT_MAP must have non-None full_name and code."""
        for key, (full_name, code) in REVERSE_DEPARTMENT_MAP.items():
            self.assertIsNotNone(
                full_name, f"REVERSE_DEPARTMENT_MAP[{key!r}] has None full_name"
            )
            self.assertIsNotNone(
                code, f"REVERSE_DEPARTMENT_MAP[{key!r}] has None code"
            )


class TestConfirmedCircularAndDepartmentOverrides(unittest.TestCase):
    """TEST 16: Verify all 60 historical and modern circular overrides requested by user."""

    def test_all_60_circular_and_department_overrides(self):
        from app.services.sebi.service import (
            KNOWN_CIRCULAR_OVERRIDES,
            KNOWN_DEPARTMENT_OVERRIDES,
        )

        user_table = [
            ("53798", "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("51628", "SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 609", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("50380", "SEBI/HO/FPI&C/P/CIR/2021/0569", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("47446", "SEBI/HO/FPI&C/CIR/P/2020/162", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("47034", "SEBI/HO/IFSC/CIR/P/2020/117", "Market Regulation Department", "MRD"),
            ("46455", "SEBI/HO/FPI&C/CIR/P/2020/056", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("36012", "SEBI/HO/MIRSD/MIRSD1/CIR/P/2017/104", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("35452", "SEBI/HO/CIR/P/2017/85", "Market Regulation Department", "MRD"),
            ("35287", "SEBI/HO/CIR/P/2017/79", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("23820", "CIR/DRMNP/4/2012", "Market Regulation Department", "MRD"),
            ("20785", "SEBI/Cir/ISD/ 05 /2011", "Integrated Surveillance Department", "ISD"),
            ("6507", None, None, None),
            ("6504", None, None, None),
            ("6461", "SEBI/IMD/CIR No.1/122201/08", "Investment Management Department", "IMD"),
            ("8308", "SEBI/VCF/Cir no. 1/ 98645 /2007", "Investment Management Department", "IMD"),
            ("14641", None, "Investment Management Department", "IMD"),
            ("14652", "SEBI/DoF/VCF/CIR No. 1/78911/2006", "Investment Management Department", "IMD"),
            ("14694", "SEBI/IMD/CIR No.6/63715/06", "Investment Management Department", "IMD"),
            ("11315", "CRD/DCR/DLT/HB/Cir-1/06", "Corporation Finance Department", "CFD"),
            ("8340", "MAPIN/Cir – 1 /2005", "Market Regulation Department", "MRD"),
            ("15543", None, "Investment Management Department", "IMD"),
            ("13151", "MAPIN/Cir – 37 /2004", "Market Regulation Department", "MRD"),
            ("12856", "MRD/DOP/MAPIN/Cir-26/2004", "Market Regulation Department", "MRD"),
            ("9550", "MIR&SD/DR-1/MSS/Cir-08/2004", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("15994", None, "Investment Management Department", "IMD"),
            ("15875", "RMB (Compendium) Series Circular No. 10", "Corporation Finance Department", "CFD"),
            ("15830", "DCC/FITTC/CIR-19/2003", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("15829", "D&CC/FITTC/CIR-20/2003", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("15826", "LGL/Cir-2/2003", "Legal Affairs Department", "LAD"),
            ("16753", "SEBI/RUW Circular No. 2(2002-2003)/UW", "Corporation Finance Department", "CFD"),
            ("16737", "D&CC/FITTC/CIR-14/2002", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("16703", "RMB Circular no. 2 (2002-03)/SAST", "Corporation Finance Department", "CFD"),
            ("16668", "RRTI Circular No 1. (2002-2003)", "Corporation Finance Department", "CFD"),
            ("16667", "RUW CIRCULAR NO.1 (2002-2003)", "Corporation Finance Department", "CFD"),
            ("16476", "SMD/DBA–II/CIR-22/2002", "Market Regulation Department", "MRD"),
            ("16465", None, "Investment Management Department", "IMD"),
            ("17952", "DCC/FITTCIR-3//2001", "Market Intermediaries Regulation and Supervision Department", "MIRSD"),
            ("17962", "SMDRPD/Policy/Cir-43/2001", "Market Regulation Department", "MRD"),
            ("17981", "RMB (Compendium) Series Circular No. 1 (2001- 2002)", "Corporation Finance Department", "CFD"),
            ("17986", "SMDRPD/Policy/Cir-37 /2001", "Market Regulation Department", "MRD"),
            ("18006", "SMDRPD/Policy/Cir-30/2001", "Market Regulation Department", "MRD"),
            ("18014", "BTI (G I Series) Circular No. 1 (2000- 2001)", "Corporation Finance Department", "CFD"),
            ("18015", "BTI (G I Series) Circular No. 1 (2000- 2001)", "Corporation Finance Department", "CFD"),
            ("18072", "IES/VCF/Cir-1/2001", "Alternative Investment Fund and Foreign Portfolio Investors Department", "AFD"),
            ("18078", "SMDRPD/Policy/Cir-8/2001", "Market Regulation Department", "MRD"),
            ("18081", "SMDRPD/Policy/Cir-6/2001", "Market Regulation Department", "MRD"),
            ("18085", "SUB-BROK/CIR/02/2001", "Market Regulation Department", "MRD"),
            ("17453", None, "Investment Management Department", "IMD"),
            ("17521", "IES/DC/CIR- 5/00", "Integrated Surveillance Department", "ISD"),
            ("17546", "SMDRPD/Policy/Cir-53/2000", "Market Regulation Department", "MRD"),
            ("17548", "SMDRPD/Policy/Cir-51/2000", "Market Regulation Department", "MRD"),
            ("17551", "SMDRPD/Policy/Cir-52/2000", "Market Regulation Department", "MRD"),
            ("17607", "DIP (Compendium) Circular No. 4", "Corporation Finance Department", "CFD"),
            ("17612", "DT CIRCULAR NO. 1( 2000 - 2001)", "Corporation Finance Department", "CFD"),
            ("17651", "MF/CIR/ 07/ 404/2000", "Investment Management Department", "IMD"),
            ("17712", "IES/DC/ 8726/00", "Integrated Surveillance Department", "ISD"),
            ("17708", "PMIMD/8755/2000", "Corporation Finance Department", "CFD"),
            ("38697", "DT CIRCULAR NO.3(1999-2000)", "Corporation Finance Department", "CFD"),
            ("17936", "RMB (Compendium) Series Circular No. 2 (1999- 2000)", "Corporation Finance Department", "CFD"),
            ("18002", "RMB (Compendium) Series Circular No. 1 (1999-2000)", "Corporation Finance Department", "CFD"),
        ]

        for cid, exp_cno, exp_dept, exp_abbr in user_table:
            with self.subTest(cid=cid):
                self.assertIn(cid, KNOWN_CIRCULAR_OVERRIDES, f"CID {cid} missing from KNOWN_CIRCULAR_OVERRIDES")
                self.assertEqual(KNOWN_CIRCULAR_OVERRIDES[cid], exp_cno)
                self.assertIn(cid, KNOWN_DEPARTMENT_OVERRIDES, f"CID {cid} missing from KNOWN_DEPARTMENT_OVERRIDES")
                act_dept, act_abbr = KNOWN_DEPARTMENT_OVERRIDES[cid]
                self.assertEqual(act_dept, exp_dept)
                self.assertEqual(act_abbr, exp_abbr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
