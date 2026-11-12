import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ctt import limits
from ctt.pricing import PriceTable
from ctt.transcripts import scan
from tests.fixtures import PRICES, build_projects


class LimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = scan(build_projects(Path(cls.temp.name) / "projects"))
        cls.prices = PriceTable(PRICES)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_window_only_counts_requests_inside(self):
        # 5h window resetting 2026-11-10T06:00Z covers 01:00–06:00Z: m2 (03:00), m3 (03:05), m4 (05:00), not m1 (11-09)
        now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        item = limits.snapshot(self.data, self.prices, "5h", 10, datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc), now)
        self.assertEqual(item.tokens, (50 + 2000 + 300) + (20 + 400) + 1_000_000)
        self.assertAlmostEqual(item.usd_per_percent, item.api_equivalent_usd / 10, places=4)

    def test_week_window_includes_older_request(self):
        now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        item = limits.snapshot(self.data, self.prices, "week", 50, datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc), now)
        self.assertEqual(item.tokens, 10 + 100 + 1000 + 500 + 2350 + 420 + 1_000_000)

    def test_append_load_summary(self):
        path = Path(self.temp.name) / "snap.jsonl"
        now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        resets = datetime(2026, 11, 12, tzinfo=timezone.utc)
        limits.append(path, [limits.snapshot(self.data, self.prices, "week", p, resets, now) for p in (2, 10, 20)])
        loaded = limits.load(path)
        self.assertEqual(len(loaded), 3)
        text = "\n".join(limits.summary(loaded))
        self.assertIn("week  2 mốc", text)  # the 2% snapshot is skipped as noise
        self.assertIn("5h    chưa có mốc", text)

    def test_zero_percent_does_not_divide(self):
        now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        item = limits.snapshot(self.data, self.prices, "5h", 0, now, now)
        self.assertEqual(item.usd_per_percent, 0.0)


if __name__ == "__main__":
    unittest.main()
