import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ctt import delegation
from ctt.cli import main
from ctt.pricing import PriceTable
from tests.fixtures import PRICES, assistant, tool, usage, write_jsonl

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
MODEL = "claude-opus-5-5"


def calls(session, prefix, start_minute, contexts, *, model=MODEL, handback=None, write_first=0):
    """One assistant request per context size, a minute apart; the first may write `write_first` new tokens."""
    rows = []
    for index, context in enumerate(contexts):
        write = write_first if index == 0 else 2_000
        rows.append(assistant(session, f"{prefix}{index}", f"r{prefix}{index}",
                              f"2026-11-12T10:{start_minute + index:02d}:00Z", model=model,
                              usage=usage(output=500, read=max(0, context - write), write_5m=write, write=write)))
    if handback:
        rows[-1]["message"]["content"] = [tool("h", "SubagentHandback", message=handback)]
    return rows


class DelegationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name)
        self.prices = PriceTable(PRICES)
        folder = self.projects / "C--work-demo"
        # Main thread: small context (100k) before the first agent, large (500k) before the second.
        write_jsonl(folder / "s1.jsonl",
                    calls("s1", "m", 0, [100_000]) + calls("s1", "n", 20, [500_000]) + calls("s1", "z", 50, [520_000] * 3))
        # Agent 1: two calls on a 100k main — a short job.
        write_jsonl(folder / "s1" / "subagents" / "agent-short.jsonl",
                    calls("s1", "a", 5, [40_000, 42_000], write_first=40_000, handback="Đã sửa 1 file."))
        # Agent 2: thirty calls on a 500k main, growing to 140k — a long job.
        write_jsonl(folder / "s1" / "subagents" / "agent-long.jsonl",
                    calls("s1", "b", 21, [40_000 + 3_000 * k for k in range(28)], write_first=40_000,
                          handback="Xong: 12 file, build pass."))

    def found(self):
        return {run.agent: run for run in delegation.runs(self.projects, self.prices, days=30, now=NOW)}

    def test_short_job_on_small_main_costs_more_long_job_on_large_main_saves(self):
        found = self.found()
        short, long = found["short"], found["long"]
        self.assertEqual((short.calls, short.main_context, short.later_main_calls), (2, 100_000, 4))
        self.assertLess(short.saving, 0)
        self.assertEqual((long.calls, long.main_context, long.growth), (28, 500_000, 81_000))
        self.assertGreater(long.saving, 0)
        self.assertEqual(long.report_tokens, round(len("Xong: 12 file, build pass.") / 2.5))

    def test_break_even_falls_as_the_main_context_grows(self):
        table = delegation.break_even(list(self.found().values()), self.prices, MODEL, MODEL)
        values = [table[context] for context in delegation.CONTEXTS]
        self.assertTrue(all(value is not None for value in values))
        self.assertEqual(values, sorted(values, reverse=True))

    def test_agents_without_requests_or_older_than_the_window_are_left_out(self):
        write_jsonl(self.projects / "C--work-demo" / "s1" / "subagents" / "agent-empty.jsonl", [])
        self.assertEqual(set(self.found()), {"short", "long"})
        self.assertEqual(delegation.runs(self.projects, self.prices, days=0, now=NOW), [])

    def test_thresholds_measured_or_from_policy(self):
        policy = {"delegation": {"min_samples": 2, "contexts": [100_000, 600_000], "sonnet": [9, 9], "same": [9, 9]}}
        measured = delegation.thresholds(self.projects, self.prices, policy, "s1", now=NOW)
        self.assertEqual((measured["source"], measured["samples"], measured["main_model"]), ("measured", 2, MODEL))
        self.assertEqual(len(measured["sonnet"]), 2)
        self.assertNotEqual(measured["sonnet"], [9, 9])
        policy["delegation"]["min_samples"] = 5
        fallback = delegation.thresholds(self.projects, self.prices, policy, now=NOW)
        self.assertEqual((fallback["source"], fallback["sonnet"], fallback["same"]), ("default", [9, 9], [9, 9]))
        self.assertEqual((fallback["nudge"], fallback["hint_model"]), (True, False))
        self.assertIsNone(delegation.session_model(self.projects, "nope"))

    def test_report_and_cli(self):
        text = delegation.report(list(self.found().values()), self.prices, 30)
        self.assertIn("1/2 agent có lời", text)
        self.assertIn("Hòa vốn — phiên chính chạy opus-5-5", text)
        self.assertIn("Chưa có agent con nào", delegation.report([], self.prices, 7))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["delegation", "--days", "3650", "--projects-dir", str(self.projects)])
        self.assertIn("Giao việc cho agent — 2 agent", out.getvalue())


if __name__ == "__main__":
    unittest.main()
