import tempfile
import unittest
from datetime import date
from pathlib import Path

from ctt.pricing import PriceTable
from ctt.sessions import (LABEL_INVESTIGATION, LABEL_LARGE, LABEL_MEDIUM, LABEL_QA, LABEL_SMALL, aggregate, label,
                          percentile)
from ctt.transcripts import Request, scan
from tests.fixtures import PRICES, build_projects


class PricingTest(unittest.TestCase):
    table = PriceTable(PRICES)

    def request(self, model, **tokens):
        values = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
        values.update(tokens)
        return Request("s", False, None, model, **values)

    def test_cost_by_token_type(self):
        usd, known = self.table.cost(self.request("claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000,
                                                  cache_read=1_000_000, cache_write_5m=1_000_000,
                                                  cache_write_1h=1_000_000))
        self.assertTrue(known)
        self.assertAlmostEqual(usd, 4 + 20 + 0.2 + 5 + 8)

    def test_dated_model_id_matches_prefix(self):
        _, known = self.table.cost(self.request("claude-haiku-4-5-20251001", output_tokens=1))
        self.assertTrue(known)

    def test_longest_prefix_wins(self):
        price, _ = self.table.lookup("claude-sonnet-5-5")
        self.assertEqual(price.cache_read, 0.1)

    def test_unknown_model_uses_fallback_and_is_flagged(self):
        usd, known = self.table.cost(self.request("claude-unknown-9", output_tokens=1_000_000))
        self.assertFalse(known)
        self.assertAlmostEqual(usd, 20)

    def test_long_prompt_uses_second_rate_card(self):
        import tomllib
        defaults = Path(__file__).resolve().parent.parent / "defaults" / "prices.toml"
        table = PriceTable(tomllib.loads(defaults.read_text(encoding="utf-8")))
        short, _ = table.cost(self.request("claude-haiku-5-5", input_tokens=50_000, output_tokens=1_000_000,
                                           cache_read=50_000))
        self.assertAlmostEqual(short, 0.10 * 0.05 + 0.50 + 0.01 * 0.05)
        long, _ = table.cost(self.request("claude-haiku-5-5", input_tokens=1_000, output_tokens=1_000_000,
                                          cache_read=100_000, cache_write_5m=1_000))
        self.assertAlmostEqual(long, 0.50 * 0.001 + 2.50 + 0.05 * 0.1 + 0.625 * 0.001)
        self.assertIs(table.lookup("claude-opus-5-5")[0].at(500_000), table.lookup("claude-opus-5-5")[0])

    def test_bad_fallback_rejected(self):
        with self.assertRaises(ValueError):
            PriceTable({"fallback": {"model": "nope"}, "models": {}})


class AggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        data = scan(build_projects(Path(cls.temp.name)))
        cls.sessions = {s.session_id: s for s in aggregate(data, PriceTable(PRICES))}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_session_totals(self):
        s1 = self.sessions["s1"]
        self.assertEqual(s1.requests, 3)
        self.assertEqual(s1.subagent_requests, 1)
        self.assertEqual(s1.output, 170)
        self.assertEqual(s1.read, 3400)
        self.assertEqual(s1.write, 800)
        self.assertEqual(s1.input, 10)  # m1 appears on two lines but is one request
        self.assertEqual(s1.total, 10 + 170 + 3400 + 800)
        self.assertTrue(s1.multi_day)
        self.assertEqual(s1.main_model, "claude-opus-5-5")

    def test_session_cost(self):
        expected = (10 * 4 + 150 * 20 + 3000 * 0.2 + 500 * 8 + 100 * 5 + 200 * 8  # opus requests
                    + 20 * 10 + 400 * 0.1) / 1e6                                  # sonnet subagent
        self.assertAlmostEqual(self.sessions["s1"].cost, expected)

    def test_source_files_and_flags(self):
        s1 = self.sessions["s1"]
        self.assertEqual(len(s1.files), 2)  # .java + migration .sql; memory-bank .md ignored
        self.assertTrue(s1.migration)
        self.assertEqual((s1.builds, s1.commits), (1, 1))
        self.assertEqual(s1.label, LABEL_SMALL)

    def test_unknown_model_counted(self):
        self.assertEqual(self.sessions["s2"].unknown_model_requests, 1)
        self.assertEqual(self.sessions["s2"].label, LABEL_QA)

    def test_since_filter(self):
        data = scan(Path(self.temp.name))
        kept = aggregate(data, PriceTable(PRICES), since=date(2026, 11, 11))
        self.assertEqual(kept, [])


class LabelTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(label(0, 29), LABEL_QA)
        self.assertEqual(label(0, 30), LABEL_INVESTIGATION)
        self.assertEqual(label(3, 1), LABEL_SMALL)
        self.assertEqual(label(4, 1), LABEL_MEDIUM)
        self.assertEqual(label(10, 1), LABEL_MEDIUM)
        self.assertEqual(label(11, 1), LABEL_LARGE)

    def test_percentile(self):
        self.assertEqual(percentile([], 0.5), 0)
        self.assertEqual(percentile([5], 0.9), 5)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertEqual(percentile(list(range(1, 11)), 0.9), 9)


if __name__ == "__main__":
    unittest.main()
