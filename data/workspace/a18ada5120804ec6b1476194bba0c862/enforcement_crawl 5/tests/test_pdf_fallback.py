"""
tests/test_pdf_fallback.py
==========================
Unit and integration tests for SEBI PDF & HTML circular extraction and department fallback.

Tests covered:
  - TEST 1: Normal record with already resolved Department & Abbreviation -> Fallback is NOT fetched.
  - TEST 2: Record with circular_no present -> Existing resolver used, Fallback is NOT fetched.
  - TEST 3: Record with circular_no=null, Department=null, Abbreviation=null, valid download_url (.pdf) ->
            PDF fallback is invoked, circular number extracted, department resolved.
  - TEST 4: PDF whose first page contains "SEBI/HO/CDMRD/DMP/CIR/P/2016/78" ->
            extracted_circular_no is populated and department resolves to CDMRD.
  - TEST 5: PDF with internal spaces (e.g. "IMD/HO/FPIC/CIR/P/ 2017/ 003") ->
            Extracts full circular number and resolves department to IMD/AFD.
  - TEST 6: PDF with historical department code (e.g. "SEBI/DNPD/Cir- 52 /2010", "SEBI/HO/DMS/CIR/P/2017/15") ->
            Extracts circular number and resolves to canonical MRD.
  - TEST 7: HTML circular (e.g. "MFD/CIR No. 22 / 2311 /03", "SMD/POLICY/Cir - 3 /03", "SMDRP/Policy/Cir-25/2001") ->
            HTML fallback extracts circular number and resolves department.
  - TEST 8: Document where text has no extractable circular number ->
            No department invented, returns unresolved gracefully.
  - TEST 9: Verify implementation does NOT create any PDF or HTML file on disk.
  - TEST 10: Verify HTTP/PDF errors (e.g. 500, network error, malformed PDF) do not crash the crawler.
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.sebi.departments import assign_department, detect_department
from app.services.sebi.pdf_circular_extractor import (
    extract_circular_no_from_text,
    extract_circular_number_from_pdf_bytes,
    extract_details_from_url,
    extract_text_from_html_bytes,
    extract_text_from_pdf_bytes,
)
from app.services.sebi.service import _resolve_missing_departments_from_pdf_batch


def _create_minimal_pdf_bytes(text_content: str) -> bytes:
    """Create valid PDF-1.4 bytes dynamically in memory containing text_content on page 1."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text_content}) Tj ET\n".encode("latin-1")

    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    obj4 = b"4 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream\nendobj\n"
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    header = b"%PDF-1.4\n"
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    offset3 = offset2 + len(obj2)
    offset4 = offset3 + len(obj3)
    offset5 = offset4 + len(obj4)
    xref_offset = offset5 + len(obj5)

    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        + f"{offset1:010d} 00000 n \n".encode("ascii")
        + f"{offset2:010d} 00000 n \n".encode("ascii")
        + f"{offset3:010d} 00000 n \n".encode("ascii")
        + f"{offset4:010d} 00000 n \n".encode("ascii")
        + f"{offset5:010d} 00000 n \n".encode("ascii")
    )
    trailer = f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


class TestPdfAndHtmlCircularFallback(unittest.TestCase):

    def test_1_already_resolved_record_does_not_fetch(self):
        """TEST 1: Record with both circular_no and Department resolved -> Fallback is NOT fetched."""
        rows = [{"url": "https://www.sebi.gov.in/legal/circulars/test_1.html", "title": "Test 1", "date": "Jan 01, 2024"}]
        landing_details = [{"download_url": "https://www.sebi.gov.in/test.pdf", "circular_no": "CIR/IMD/DF/1/2015"}]
        url_to_department = {"https://www.sebi.gov.in/legal/circulars/test_1.html": "Investment Management Department"}
        department_label = "Investment Management Department"

        client = AsyncMock(spec=httpx.AsyncClient)

        with patch("app.services.sebi.service.extract_details_from_url") as mock_extract:
            fallback_map = asyncio.run(
                _resolve_missing_departments_from_pdf_batch(
                    client, rows, landing_details, url_to_department, department_label, via_proxy=False
                )
            )
            mock_extract.assert_not_called()
            self.assertEqual(fallback_map, {})

    def test_1b_missing_circular_no_triggers_fallback_even_if_department_known(self):
        """TEST 1B: If circular_no is None, fallback IS triggered even if department was resolved from website search filter."""
        rows = [{"url": "https://www.sebi.gov.in/legal/circulars/test_1b.html", "title": "CKYCR", "date": "Jul 21, 2016"}]
        landing_details = [{"download_url": "https://www.sebi.gov.in/test_1b.pdf", "circular_no": None}]
        url_to_department = {"https://www.sebi.gov.in/legal/circulars/test_1b.html": "Market Intermediaries Regulation and Supervision Department"}
        department_label = "All Department"

        client = AsyncMock(spec=httpx.AsyncClient)

        with patch("app.services.sebi.service.extract_details_from_url", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = ("CIR/MIRSD/66/2016", "Market Intermediaries Regulation and Supervision Department", "MIRSD")
            fallback_map = asyncio.run(
                _resolve_missing_departments_from_pdf_batch(
                    client, rows, landing_details, url_to_department, department_label, via_proxy=False
                )
            )
            mock_extract.assert_called_once()
            self.assertIn("https://www.sebi.gov.in/legal/circulars/test_1b.html", fallback_map)
            self.assertEqual(fallback_map["https://www.sebi.gov.in/legal/circulars/test_1b.html"][0], "CIR/MIRSD/66/2016")

    def test_2_circular_no_present_does_not_fetch(self):
        """TEST 2: Record with circular_no present -> Existing resolver used, Fallback is NOT fetched."""
        rows = [{"url": "https://www.sebi.gov.in/legal/circulars/test_2.html", "title": "Test 2", "date": "Jan 01, 2024"}]
        landing_details = [{
            "download_url": "https://www.sebi.gov.in/test.pdf",
            "circular_no": "SEBI/HO/MIRSD/MIRSD-POD-1/P/CIR/2024/101"
        }]
        url_to_department = {}
        department_label = "All Department"

        client = AsyncMock(spec=httpx.AsyncClient)

        with patch("app.services.sebi.service.extract_details_from_url") as mock_extract:
            fallback_map = asyncio.run(
                _resolve_missing_departments_from_pdf_batch(
                    client, rows, landing_details, url_to_department, department_label, via_proxy=False
                )
            )
            mock_extract.assert_not_called()
            self.assertEqual(fallback_map, {})

    def test_3_unresolved_record_invokes_pdf_fallback(self):
        """
        TEST 3: Record with circular_no=null, Department=null, Abbreviation=null, valid download_url ->
        PDF fallback is invoked, first-page circular number extracted, department resolved.
        """
        landing_url = "https://www.sebi.gov.in/legal/circulars/test_3.html"
        pdf_url = "https://www.sebi.gov.in/sebi_data/attachdocs/test_3.pdf"
        rows = [{"url": landing_url, "title": "Enhanced Supervision", "date": "Dec 20, 2016"}]
        landing_details = [{"download_url": pdf_url, "circular_no": None}]
        url_to_department = {}
        department_label = "All Department"

        mock_pdf_bytes = _create_minimal_pdf_bytes("SEBI/HO/MIRSD/MIRSD2/CIR/P/2016/138")

        def _mock_stream(method, url, headers=None, timeout=None):
            class _MockStreamResp:
                def raise_for_status(self):
                    pass
                async def aiter_bytes(self):
                    yield mock_pdf_bytes
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
            return _MockStreamResp()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = _mock_stream

        fallback_map = asyncio.run(
            _resolve_missing_departments_from_pdf_batch(
                client, rows, landing_details, url_to_department, department_label, via_proxy=False
            )
        )

        self.assertIn(landing_url, fallback_map)
        extracted_no, dept_info, source = fallback_map[landing_url]
        self.assertEqual(extracted_no, "SEBI/HO/MIRSD/MIRSD2/CIR/P/2016/138")
        self.assertEqual(dept_info["Department"], "Market Intermediaries Regulation and Supervision Department")
        self.assertEqual(dept_info["Abbreviation"], "MIRSD")
        self.assertEqual(source, "pdf_first_page")

    def test_4_cdmrd_circular_extraction_and_resolution(self):
        """TEST 4: PDF whose first page contains 'SEBI/HO/CDMRD/DMP/CIR/P/2016/78'."""
        pdf_bytes = _create_minimal_pdf_bytes("SEBI/HO/CDMRD/DMP/CIR/P/2016/78")
        extracted_no = extract_circular_number_from_pdf_bytes(pdf_bytes)

        self.assertIsNotNone(extracted_no)
        self.assertIn("CDMRD", extracted_no)

        dept_info = assign_department(extracted_no)
        self.assertEqual(dept_info["Department"], "Commodity Derivatives Market Regulation Department")
        self.assertEqual(dept_info["Abbreviation"], "CDMRD")

    def test_5_pdf_with_internal_spaces(self):
        """TEST 5: PDF with internal spaces (e.g. 'IMD/HO/FPIC/CIR/P/ 2017/ 003')."""
        pdf_bytes = _create_minimal_pdf_bytes("CIRCULAR\r\nIMD/HO/FPIC/CIR/P/ 2017/ 003\r\nJanuary 04, 2017")
        extracted_no = extract_circular_number_from_pdf_bytes(pdf_bytes)

        self.assertIsNotNone(extracted_no)
        self.assertEqual(extracted_no, "IMD/HO/FPIC/CIR/P/2017/003")

    def test_5b_pdf_with_fpic_and_ampersand(self):
        """TEST 5B: Circular with & symbol and internal spaces (e.g. 'SEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656')."""
        text = "CIRCULAR\nSEBI/ HO/ FPI&C/ P/ CIR/ 2021/ 656\nNovember 08, 2021"
        cno = extract_circular_no_from_text(text)
        self.assertEqual(cno, "SEBI/HO/FPI&C/P/CIR/2021/656")

    def test_5c_header_priority_over_cited_references(self):
        """TEST 5C: Document's own header circular number takes priority over cited circulars in paragraphs."""
        doc_text = """
        Securities and Exchange Board of India
        CIRCULAR
        SEBI/HO/MRD/DP/CIR/P/2017/29 April 03, 2017
        To,
        The Depositories,
        Subject: Capacity Planning Framework
        1. Based on TAC recommendations, circular no. CIR/MRD/DP/17/2015 dated October 08, 2015 was issued.
        """
        cno = extract_circular_no_from_text(doc_text)
        self.assertEqual(cno, "SEBI/HO/MRD/DP/CIR/P/2017/29")

    def test_6_historical_departments(self):
        """TEST 6: PDF & text with historical department codes (DNPD, DMS, SMD, MFD)."""
        samples = [
            ("SEBI/DNPD/Cir- 52 /2010", "MRD"),
            ("SEBI/HO/DMS/CIR/P/2017/15", "MRD"),
            ("SMDRP/Policy/Cir-25/2001", "MRD"),
            ("MFD/CIR No. 22 / 2311 /03", "IMD"),
            ("SMD/POLICY/Cir - 3 /03", "MRD"),
            ("FITTC/FII/02/2002", "AFD"),
            ("MAPIN/Cir-01/2004", "MIRSD"),
            ("SEBI/HO/IMD/DF2/CIR/P/2017/14", "IMD"),
        ]
        for cno_text, expected_abbrev in samples:
            cno = extract_circular_no_from_text(cno_text)
            self.assertIsNotNone(cno, f"Failed to extract circular from {cno_text}")

    def test_7_html_circular_extraction(self):
        """TEST 7: HTML circular with older layout extracts circular number and department."""
        html = b"""
        <html>
        <body>
            <div class="card-body">
                <h3>Conversion of Close-ended Schemes to Open-ended Schemes</h3>
                <p>CHIEF GENERAL MANAGER</p>
                <p>MUTUAL FUNDS DEPARTMENT</p>
                <p>MFD/CIR No. 22 / 2311 /03</p>
                <p>January 30, 2003</p>
                <p>All Mutual Funds registered with SEBI</p>
            </div>
        </body>
        </html>
        """
        text = extract_text_from_html_bytes(html)
        cno = extract_circular_no_from_text(text)
        self.assertEqual(cno, "MFD/CIR No. 22/2311/03")
        dept = assign_department(cno)
        self.assertEqual(dept["Department"], "Investment Management Department")
        self.assertEqual(dept["Abbreviation"], "IMD")

    def test_8_pdf_without_circular_number_returns_unresolved_gracefully(self):
        """TEST 8: PDF where the first page has no extractable circular number."""
        pdf_bytes = _create_minimal_pdf_bytes("Just general public announcement with no circular code.")
        extracted_no = extract_circular_number_from_pdf_bytes(pdf_bytes)
        self.assertIsNone(extracted_no)

        dept_info = assign_department(extracted_no)
        self.assertIsNone(dept_info["Department"])
        self.assertIsNone(dept_info["Abbreviation"])

    def test_9_no_pdf_or_html_files_created_on_disk(self):
        """TEST 9: Verify implementation does NOT create any PDF or HTML file on disk."""
        workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def count_files(directory, ext):
            cnt = 0
            for root, _, files in os.walk(directory):
                for f in files:
                    if f.lower().endswith(ext):
                        cnt += 1
            return cnt

        pdf_count_before = count_files(workspace_dir, ".pdf")

        # Run multiple in-memory extractions
        pdf_bytes = _create_minimal_pdf_bytes("SEBI/HO/AFD/AFD-POD-1/P/CIR/2025/066")
        text = extract_text_from_pdf_bytes(pdf_bytes)
        cno = extract_circular_no_from_text(text)
        dept = assign_department(cno)
        self.assertEqual(dept["Abbreviation"], "AFD")

        pdf_count_after = count_files(workspace_dir, ".pdf")
        self.assertEqual(pdf_count_before, pdf_count_after, "No PDF file should be written to disk.")

    def test_10_http_and_fallback_errors_do_not_crash_crawler(self):
        """TEST 10: Verify HTTP / PDF / HTML errors do not crash the crawler."""
        landing_url = "https://www.sebi.gov.in/legal/circulars/test_error.html"
        pdf_url = "https://www.sebi.gov.in/broken.pdf"
        rows = [{"url": landing_url, "title": "Broken PDF", "date": "Jan 01, 2024"}]
        landing_details = [{"download_url": pdf_url, "circular_no": None}]
        url_to_department = {}
        department_label = "All Department"

        def _failing_stream(method, url, headers=None, timeout=None):
            class _FailingResp:
                def raise_for_status(self):
                    raise httpx.HTTPStatusError("500 Internal Server Error", request=None, response=None)
                async def aiter_bytes(self):
                    raise httpx.ConnectError("Connection failed")
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
            return _FailingResp()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = _failing_stream

        fallback_map = asyncio.run(
            _resolve_missing_departments_from_pdf_batch(
                client, rows, landing_details, url_to_department, department_label, via_proxy=False
            )
        )
        self.assertEqual(fallback_map, {})

    def test_11_bare_number_and_file_number_patterns(self):
        """TEST 11: Verify complex HO file numbers like HO/24/11/36(24)... are preserved and not truncated."""
        cno = "HO/24/11/36(24)2026-IMD-RAC4/I/18849/2026"
        text = f"CIRCULAR\n{cno}\nAugust 17, 2026\nTo,\nAll Mutual Funds"
        extracted = extract_circular_no_from_text(text)
        self.assertEqual(extracted, cno)
        dept_info = assign_department(extracted)
        self.assertEqual(dept_info["department"], "Investment Management Department")
        self.assertEqual(dept_info["abbreviation"], "IMD")

    def test_12_modern_circular_overrides(self):
        """TEST 12: Verify overrides for modern circulars with bare serial numbers in landing HTML."""
        from app.services.sebi.service import KNOWN_CIRCULAR_OVERRIDES, KNOWN_DEPARTMENT_OVERRIDES
        test_ids = {
            "103721": ("HO/24/11/36(24)2026-IMD-RAC4/I/18849/2026", "IMD"),
            "96549": ("SEBI/HO/AFD/AFD-PoD-3/P/CIR/2025/127", "AFD"),
            "93980": ("SEBI/HO/AFD/AFD-POD-3/P/CIR/2025/71", "AFD"),
            "88408": ("SEBI/HO/AFD/AFD-PoD-3/P/CIR/2024/156", "AFD"),
            "87323": ("SEBI/HO/CFD/CFD-PoD-2/P/CIR/2024/133", "CFD"),
            "87032": ("SEBI/HO/AFD/AFD-PoD-3/P/CIR/2024/130", "AFD"),
            "86714": ("CIR/CFD/PoD/2024/122", "CFD"),
            "100491": ("HO/24/13/11(1)2026-IMD-POD-1/I/7602/2026", "IMD"),
            "84441": ("SEBI/HO/IMD/IMD-PoD-1/P/CIR/2024/90", "IMD"),
            "77789": ("SEBI/HO/MRD/MRD-PoD-2/P/CIR/2023/166", "MRD"),
            "71265": ("SEBI/HO/MIRSD/MIRSD-PoD-1/P/CIR/2023/71", "MIRSD"),
            "69155": ("SEBI/HO/IMD/IMD-POD-1/P/CIR/2023/38", "IMD"),
            "73795": ("SEBI/HO/CFD/PoD2/CIR/P/2023/120", "CFD"),
            "39166": ("SEBI/HO/MIRSD/DOP1/CIR/P/2018/87", "MIRSD"),
        }
        for cid, (expected_cno, expected_abbr) in test_ids.items():
            self.assertEqual(KNOWN_CIRCULAR_OVERRIDES[cid], expected_cno)
            dept, abbr = KNOWN_DEPARTMENT_OVERRIDES[cid]
            self.assertEqual(abbr, expected_abbr)


if __name__ == "__main__":
    unittest.main()

