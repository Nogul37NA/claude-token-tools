import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ctt import hooks
from ctt.estimate import engine, ledger, render
from ctt.estimate.profile import ProfileError, parse
from ctt.pricing import PriceTable
from tests.fixtures import PRICES, assistant, usage, write_jsonl

RISKS = engine.load_risks()
SETTINGS = engine.load_settings()
POLICY = {"roles": {"research": {"model": "sonnet", "effort": "medium"}, "developer": {"model": "sonnet", "effort": "medium"},
                    "tester": {"model": "sonnet", "effort": "medium"}, "reviewer": {"model": "opus", "effort": "medium"},
                    "debugger": {"model": "opus", "effort": "high"}}}
TABLE = PriceTable(PRICES)
BASE = {"title": "Demo", "kind": "change_existing", "scope": {"apis": 2, "impacted_classes": 6}}


def profile(**changes):
    return parse({**BASE, **changes}, RISKS)


def run(task, sessions=(), usd_per_percent=None):
    return engine.estimate(task, list(sessions), TABLE, POLICY, SETTINGS, RISKS, usd_per_percent,
                           now=datetime(2026, 11, 12, tzinfo=timezone.utc))


class ProfileTest(unittest.TestCase):
    def test_reports_every_problem(self):
        with self.assertRaises(ProfileError) as caught:
            parse({"kind": "bad", "title": "", "scope": {"apis": -1, "pages": 2}, "risks": ["nope"]}, RISKS)
        text = str(caught.exception)
        for fragment in ("kind:", "title:", "scope.apis", "scope.pages", "'nope'"):
            self.assertIn(fragment, text)

    def test_product_needs_features(self):
        with self.assertRaises(ProfileError):
            parse({**BASE, "kind": "product"}, RISKS)


class EngineTest(unittest.TestCase):
    def test_files_and_label(self):
        factors = SETTINGS["files"]
        self.assertEqual(engine.files_estimate("change_existing", {"apis": 2, "impacted_classes": 6}, factors), 5)
        self.assertEqual(engine.files_estimate("new_feature", {"screens": 3, "apis": 2, "entities": 1}, factors), 13)
        self.assertEqual(engine.files_estimate("change_existing", {"files_estimate": 2, "apis": 9}, factors), 2)
        self.assertEqual([engine.label_code("change_existing", n) for n in (0, 3, 4, 10, 11)],
                         ["small", "small", "medium", "medium", "large"])

    def test_auto_risks(self):
        found = engine.detect_risks(["money"], {"migrations": 1, "integrations": 1, "impacted_classes": 20},
                                    "prod_hotfixes", ["a", "b", "c"])
        self.assertEqual(found, ["money", "wide_impact", "flyway", "external_dependency", "prod_hotfix",
                                 "unclear_requirements"])

    def test_fallback_numbers_and_model_ratio(self):
        est = run(profile(risks=["shared_query", "no_tests"]))  # 3 points → mid, ×1.2; medium group via fallback
        fallback = SETTINGS["fallback"]["medium"]
        self.assertAlmostEqual(est["tokens"]["p50"], fallback["p50_tokens"])
        self.assertAlmostEqual(est["tokens"]["p90"], fallback["p90_tokens"] * 1.2)
        self.assertAlmostEqual(est["tokens"]["budget"], fallback["p90_tokens"] * 1.2 * 1.2)
        part = est["parts"][0]
        self.assertEqual(part["risk_level"], "mid")
        developer = next(s for s in part["steps"] if s["role"] == "developer")
        self.assertEqual((developer["model"], developer["effort"]), ("opus", "high"))  # hard + mid risk → Opus
        research = next(s for s in part["steps"] if s["role"] == "research")
        self.assertAlmostEqual(research["p50_usd"], fallback["p50_usd"] * 0.15 * 0.5)   # sonnet = half of opus
        self.assertEqual(est["confidence"], "low")  # fallback group
        self.assertIn("Người duyệt trước commit", part["gates"])

    def test_small_low_risk_keeps_sonnet(self):
        est = run(profile(scope={"apis": 1}))
        developer = next(s for s in est["parts"][0]["steps"] if s["role"] == "developer")
        self.assertEqual((developer["model"], developer["effort"]), ("sonnet", "medium"))
        self.assertEqual(est["parts"][0]["label"], "small")

    def test_product_combines_in_quadrature(self):
        task = parse({"title": "Cổng", "kind": "product", "scope": {}, "features": [
            {"name": "F1", "kind": "new_feature", "scope": {"screens": 2, "apis": 1}},
            {"name": "F2", "kind": "new_feature", "scope": {"screens": 1}}]}, RISKS)
        est = run(task)
        p1, p2 = est["parts"]
        self.assertAlmostEqual(est["tokens"]["p50"], p1["p50_tokens"] + p2["p50_tokens"])
        spread = math.hypot(p1["p90_tokens"] - p1["p50_tokens"], p2["p90_tokens"] - p2["p50_tokens"])
        self.assertAlmostEqual(est["tokens"]["p90"], est["tokens"]["p50"] + spread)
        self.assertLess(est["tokens"]["p90"], p1["p90_tokens"] + p2["p90_tokens"])

    def test_weekly_percent_and_investigation_note(self):
        est = run(profile(kind="investigation"), usd_per_percent=2.0)
        self.assertAlmostEqual(est["weekly_percent"]["p90"], est["usd"]["p90"] / 2.0)
        self.assertTrue(any("chẩn đoán" in a for a in est["assumptions"]))
        self.assertEqual({s["role"] for s in est["parts"][0]["steps"]}, {"research", "debugger"})

    def test_render_has_scale_and_card(self):
        est = run(profile(), usd_per_percent=2.0)
        page = render.html_page(est, weekly_used=65)
        self.assertIn("P50", page)
        self.assertIn("Ngân sách", page)
        self.assertIn("65% đã dùng", page)
        self.assertIn("| Token |", render.text_card(est))


class LedgerAndHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CTT_HOME": self.temp.name})
        self.env.start()
        self.transcript = Path(self.temp.name) / "projects" / "C--work-demo" / "sess1.jsonl"
        write_jsonl(self.transcript, [assistant("sess1", "m0", "r0", "2026-11-12T01:00:00Z",
                                                usage=usage(read=1_000_000))])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_current_session_by_cwd(self):
        found = ledger.current_session(Path(self.temp.name) / "projects", "C:\\work\\demo")
        self.assertEqual(found[0], "sess1")
        self.assertEqual(found[2], self.transcript.stat().st_size)

    def test_running_session_falls_back_to_the_transcript_just_written(self):
        projects = Path(self.temp.name) / "projects"
        # the project opened at cwd wins, however old its transcript
        self.assertEqual(ledger.running_session(projects, "C:\\work\\demo", now=self.transcript.stat().st_mtime + 9e6)[0],
                         "sess1")
        # a work folder that is no session's own: the transcript written within the window
        now = self.transcript.stat().st_mtime + 30
        found = ledger.running_session(projects, "C:\\elsewhere\\build", now=now)
        self.assertEqual((found[0], found[1], found[2]), ("sess1", str(self.transcript), self.transcript.stat().st_size))
        self.assertIsNone(ledger.running_session(projects, "C:\\elsewhere\\build", now=now + 600))  # nobody is running
        self.assertIsNone(ledger.running_session(projects / "missing", "C:\\elsewhere\\build"))

    def test_usage_since_reads_only_new_complete_lines(self):
        start = self.transcript.stat().st_size
        with self.transcript.open("a", encoding="utf-8") as handle:
            line = json.dumps(assistant("sess1", "m1", "r1", "2026-11-12T01:05:00Z", usage=usage(read=500_000)))
            handle.write(line + "\n" + line + "\n")          # same request written twice
            handle.write('{"partial": ')                     # line still being written
        tokens, _, offset, _ = ledger.usage_since(self.transcript, start, TABLE)
        self.assertEqual(tokens, 500_000)
        self.assertEqual(offset, self.transcript.stat().st_size - len('{"partial": '))

    def test_budget_warnings_once_per_checkpoint(self):
        est = run(profile(files_estimate=None, scope={"files_estimate": 1}))
        est["tokens"]["budget"] = 1_000_000
        start = self.transcript.stat().st_size
        ledger.append({"event": "created", "estimate": est, "session_id": "sess1",
                       "transcript_path": str(self.transcript), "start_offset": start})
        data = {"session_id": "sess1", "transcript_path": str(self.transcript)}
        state = {}
        self.assertIsNone(hooks.budget_message(data, state))  # not approved yet
        ledger.append({"event": "approved", "id": est["id"], "at": "2026-11-12T01:01:00Z"})

        def add(message_id, read):
            with self.transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(assistant("sess1", message_id, message_id, "2026-11-12T01:10:00Z",
                                                  usage=usage(read=read))) + "\n")

        add("a", 600_000)
        text, level = hooks.budget_message(data, state)
        self.assertEqual(level, 0.5)
        self.assertIn("60%", text)
        self.assertIsNone(hooks.budget_message(data, state))   # no new checkpoint
        add("b", 500_000)
        text, level = hooks.budget_message(data, state)
        self.assertEqual(level, 1.0)                          # crossed 80% and 100% at once → one message
        self.assertIn("hết ngân sách", text)
        self.assertEqual(state["budget"]["warned"], [0.5, 0.8, 1.0])

    def test_check_summary(self):
        rows = [{"actual": 10, "within_p90": True, "ratio": 0.8}, {"actual": 10, "within_p90": False, "ratio": 2.0},
                {"actual": 0, "within_p90": True, "ratio": 0}]
        self.assertIn("1/2 (50%)", ledger.check_summary(rows))


if __name__ == "__main__":
    unittest.main()
