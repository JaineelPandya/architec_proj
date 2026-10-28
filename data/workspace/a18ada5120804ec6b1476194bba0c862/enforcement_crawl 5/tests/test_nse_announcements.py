import unittest
from datetime import date

from app.services.nse.announcements.schema import AnnouncementQuery
from app.services.nse.announcements.service import _parse_item


class TestNseAnnouncements(unittest.TestCase):
    def test_query_accepts_dd_mm_yyyy(self):
        q = AnnouncementQuery(from_date="01-10-2026", to_date="15-10-2026")
        self.assertEqual(q.from_date, date(2026, 10, 1))
        self.assertEqual(q.to_date, date(2026, 10, 15))

    def test_query_accepts_yyyy_mm_dd(self):
        q = AnnouncementQuery(from_date="2026-10-01", to_date="2026-10-15")
        self.assertEqual(q.from_date, date(2026, 10, 1))
        self.assertEqual(q.to_date, date(2026, 10, 15))

    def test_item_formats_broadcast_dates_to_dd_mm_yyyy(self):
        raw_item = {
            "seq_id": 999,
            "symbol": "INFY",
            "sm_name": "Infosys Limited",
            "desc": "Financial Results",
            "attchmntText": "Q2 results",
            "attchmntFile": "https://example.com/results.pdf",
            "an_dt": "07-Oct-2026 16:12:54",
        }
        res = _parse_item(raw_item, "equities")
        self.assertEqual(res.broadcast_date, "07-10-2026")
        self.assertEqual(res.broadcast_datetime, "07-10-2026 16:12:54")


if __name__ == "__main__":
    unittest.main()
