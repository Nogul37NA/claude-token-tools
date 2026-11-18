import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt import ship
from ctt.cli import main

LIMITS = {**ship.DEFAULTS, "max_children": 4, "max_depth": 2}


class ShipTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"CTT_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(list(argv))
        return out.getvalue()

    def test_settings_default_override_and_refuse(self):
        self.assertEqual(ship.settings({}), ship.DEFAULTS)
        self.assertEqual(ship.settings({"workflow": {"auto_approve_usd": 2}})["auto_approve_usd"], 2.0)
        for bad in ({"max_steps": 2.5}, {"clarify_max": -1}, {"fix_rounds": True}):
            with self.assertRaises(ValueError):
                ship.settings({"workflow": bad})

    def test_plan_checks_caps_and_modes(self):
        steps = [{"name": "API", "role": "developer", "mode": "session", "tokens": 3_000_000},
                 {"name": "Test", "role": "tester", "mode": "subagent"}]
        plan = ship.check_plan(steps, LIMITS)
        self.assertEqual([s["status"] for s in plan], ["pending", "pending"])
        with self.assertRaisesRegex(ValueError, "session_min_tokens"):
            ship.check_plan([{"name": "nhỏ", "mode": "session", "tokens": 100_000}], LIMITS)
        with self.assertRaisesRegex(ValueError, "max_steps"):
            ship.check_plan([{"name": f"b{i}"} for i in range(9)], LIMITS)
        with self.assertRaisesRegex(ValueError, "max_children"):
            ship.check_plan([{"name": f"s{i}", "mode": "session"} for i in range(5)], {**LIMITS, "max_steps": 9})
        with self.assertRaisesRegex(ValueError, "mode"):
            ship.check_plan([{"name": "x", "mode": "cloud"}], LIMITS)

    def test_run_through_the_stages(self):
        run = ship.start("Thêm API xuất Excel", "s-1", "C:/repo", now="2026-11-12T09:00:00+00:00")
        self.assertEqual(run["id"], "2026-11-12-them-api-xuat-excel")
        self.assertEqual(ship.start("Thêm API xuất Excel", now="2026-11-12T09:00:00+00:00")["id"],
                         "2026-11-12-them-api-xuat-excel-2")
        ship.set_stage(run["id"], 3, estimate_id="est-1",
                       plan=[{"name": "API", "role": "developer", "mode": "subagent"}, {"name": "Test", "mode": "subagent"}],
                       limits=LIMITS)
        code = ship.set_step(run["id"], "API", "running")["plan"][0]["attempt"]
        brief = ship.brief(ship.for_session("s-1"))
        self.assertEqual((brief["stage"], brief["stage_name"], brief["steps"], brief["running"]),
                         (3, "Chạy", 2, ["API"]))
        ship.set_step(run["id"], "API", "done", attempt=code)
        done = ship.finish(run["id"], "ok", check={"p50": 4e6, "p90": 9e6, "actual": 5e6, "ratio": 1.25,
                                                   "within_p90": True, "title": "x"})
        self.assertEqual((done["stage"], done["result"], done["check"]["ratio"]), (6, "ok", 1.25))
        self.assertIn("×1.25", ship.summary(done))
        with self.assertRaisesRegex(ValueError, "không có bước"):
            ship.set_step(run["id"], "Ghost", "done")
        with self.assertRaises(ValueError):
            ship.load("../evil")
        self.assertIsNone(ship.for_session("nobody"))

    def _run_with(self, steps, session="s-1"):
        run = ship.start("Việc", session, "C:/repo")
        return ship.set_stage(run["id"], 3, plan=steps, limits=LIMITS)["id"]

    def test_plan_needs_unknown_self_duplicate_and_loop(self):
        plan = ship.check_plan([{"name": "A"}, {"name": "B", "needs": ["A", "A"]}], LIMITS)
        self.assertEqual([s["needs"] for s in plan], [[], ["A"]])
        for steps, message in (
                ([{"name": "A", "needs": ["Ghost"]}], "không có trong kế hoạch"),
                ([{"name": "A", "needs": ["A"]}], "chính nó"),
                ([{"name": "A"}, {"name": "A"}], "trùng tên"),
                ([{"name": "A", "needs": "B"}, {"name": "B"}], "danh sách"),
                ([{"name": "A", "needs": ["C"]}, {"name": "B", "needs": ["A"]}, {"name": "C", "needs": ["B"]}], "vòng")):
            with self.assertRaisesRegex(ValueError, message):
                ship.check_plan(steps, LIMITS)

    def test_next_steps_follow_needs(self):
        run_id = self._run_with([{"name": "API"}, {"name": "UI"}, {"name": "Test", "needs": ["API", "UI"]}])
        nxt = ship.next_steps(ship.load(run_id))
        self.assertEqual([s["name"] for s in nxt["ready"]], ["API", "UI"])
        self.assertEqual(nxt["waiting"], [{"name": "Test", "on": ["API", "UI"]}])
        with self.assertRaisesRegex(ValueError, "còn chờ: API, UI"):
            ship.set_step(run_id, "Test", "running")
        code = ship.set_step(run_id, "API", "running")["plan"][0]["attempt"]
        ship.set_step(run_id, "API", "done", attempt=code)
        ship.set_step(run_id, "UI", "skipped")
        nxt = ship.next_steps(ship.load(run_id))
        self.assertEqual(([s["name"] for s in nxt["ready"]], nxt["waiting"], nxt["all_over"]), (["Test"], [], False))
        ship.set_step(run_id, "Test", "done")
        self.assertTrue(ship.next_steps(ship.load(run_id))["all_over"])

    def test_attempt_codes_refuse_a_stale_report(self):
        run_id = self._run_with([{"name": "API"}])
        first = ship.set_step(run_id, "API", "running")["plan"][0]["attempt"]
        self.assertRegex(first, r"^a1-[0-9a-f]{4}$")
        with self.assertRaisesRegex(ValueError, f"--attempt {first}"):
            ship.set_step(run_id, "API", "done")
        ship.set_step(run_id, "API", "failed", attempt=first, note="test trượt")
        second = ship.set_step(run_id, "API", "running")["plan"][0]["attempt"]
        self.assertTrue(second.startswith("a2-"))
        with self.assertRaisesRegex(ValueError, "lần thử cũ"):
            ship.set_step(run_id, "API", "done", attempt=first)
        with self.assertRaisesRegex(ValueError, "không có"):
            ship.set_step(run_id, "API", "done", attempt="a9-zzzz")
        self.assertEqual(ship.load(run_id)["plan"][0]["status"], "running")  # refused reports change nothing
        step = ship.set_step(run_id, "API", "done", attempt=second, files=["src/a.py", "src/b.py"],
                             tests="12 pass, 0 fail")["plan"][0]
        self.assertEqual((step["status"], step["attempt"], step["attempts"]), ("done", None, 2))
        self.assertEqual([t["status"] for t in step["tries"]], ["failed", "done"])
        self.assertEqual(step["report"], {"files": ["src/a.py", "src/b.py"], "tests": "12 pass, 0 fail"})
        with self.assertRaisesRegex(ValueError, "đã đóng"):
            ship.set_step(run_id, "API", "failed", attempt=second)
        run = ship.load(run_id)
        self.assertEqual(ship.brief(run)["retries"], 1)
        self.assertIn("2 lần thử · 2 file · test: 12 pass, 0 fail", ship.summary(run))

    def test_restart_and_reset_give_up_the_open_try(self):
        run_id = self._run_with([{"name": "API"}])
        first = ship.set_step(run_id, "API", "running")["plan"][0]["attempt"]
        step = ship.set_step(run_id, "API", "running")["plan"][0]
        self.assertEqual((step["attempts"], step["tries"][0]["status"]), (2, "abandoned"))
        with self.assertRaisesRegex(ValueError, "lần thử cũ"):
            ship.set_step(run_id, "API", "done", attempt=first)
        step = ship.set_step(run_id, "API", "pending")["plan"][0]
        self.assertEqual((step["attempt"], step["tries"][1]["status"]), (None, "abandoned"))

    def test_run_written_before_attempt_codes_still_works(self):
        run_id = self._run_with([{"name": "API"}])
        run = ship.load(run_id)
        run["plan"] = [{"name": "API", "role": None, "mode": "main", "files": [], "done": "", "tokens": None,
                        "status": "running"}]  # 0.18 shape: no needs, no attempt
        ship.save(run)
        self.assertEqual(ship.next_steps(run)["running"], [{"name": "API", "attempt": None}])
        self.assertEqual(ship.set_step(run_id, "API", "done")["plan"][0]["status"], "done")

    def test_resume_attaches_the_run_and_says_what_is_next(self):
        run_id = self._run_with([{"name": "API"}, {"name": "UI"}, {"name": "Test", "needs": ["API", "UI"]}], "old")
        code = ship.set_step(run_id, "API", "running")["plan"][0]["attempt"]
        self.assertEqual([r["id"] for r in ship.unfinished()], [run_id])
        text = self.run_cli("ship-resume", run_id, "--session", "new")
        self.assertIn(f"API [{code}]", text)
        self.assertIn("Chạy được ngay: UI", text)
        self.assertIn("Test ← API, UI", text)
        run = ship.load(run_id)
        self.assertEqual((run["session_id"], run["past_sessions"]), ("new", ["old"]))
        self.assertEqual(ship.for_session("new")["id"], run_id)
        self.assertIn(run_id, self.run_cli("ship-resume"))
        ship.finish(run_id, "partial")
        self.assertEqual(ship.unfinished(), [])
        self.assertIn("Không có lượt nào đang dở", self.run_cli("ship-resume"))
        self.assertIn("đã kết thúc", self.run_cli("ship-resume", run_id, "--session", "third"))
        self.assertEqual(ship.load(run_id)["session_id"], "new")  # a finished run is not taken over

    def test_cli_step_attempt_files_and_next(self):
        run_id = self._run_with([{"name": "API"}, {"name": "Test", "needs": ["API"]}])
        self.assertIn("Chạy được ngay: API", self.run_cli("ship-next", run_id))
        out = self.run_cli("ship-step", run_id, "--name", "API", "--status", "running")
        code = ship.load(run_id)["plan"][0]["attempt"]
        self.assertIn(f"--attempt {code}", out)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_cli("ship-step", run_id, "--name", "API", "--status", "done")
        self.assertIn(code, err.getvalue())
        out = json.loads(self.run_cli("ship-step", run_id, "--name", "API", "--status", "done", "--attempt", code,
                                      "--file", "a.py", "--file", "b.py", "--tests", "3 pass", "--json"))
        self.assertEqual(out["plan"][0]["report"], {"files": ["a.py", "b.py"], "tests": "3 pass"})
        nxt = json.loads(self.run_cli("ship-next", run_id, "--json"))
        self.assertEqual(([s["name"] for s in nxt["ready"]], nxt["waiting"]), (["Test"], []))

    def test_cli_start_stage_step_finish(self):
        started = json.loads(self.run_cli("ship-start", "--title", "Trang báo cáo", "--session", "s-9", "--cwd", "C:/r"))
        run_id = started["run"]["id"]
        self.assertEqual(started["workflow"]["max_children"], 4)
        self.assertEqual(len(started["stages"]), 7)
        plan = Path(self.home.name, "plan.json")
        plan.write_text(json.dumps({"steps": [{"name": "UI", "role": "developer", "mode": "subagent"}]}), encoding="utf-8")
        self.assertIn("[pending] UI", self.run_cli("ship-stage", run_id, "--stage", "3", "--plan", str(plan)))
        out = json.loads(self.run_cli("ship-step", run_id, "--name", "UI", "--status", "done", "--json"))
        self.assertEqual(out["plan"][0]["status"], "done")
        projects = Path(self.home.name, "projects")
        projects.mkdir()
        out = json.loads(self.run_cli("ship-finish", run_id, "--result", "partial", "--json", "--projects-dir", str(projects)))
        self.assertEqual((out["stage"], out["result"]), (6, "partial"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_cli("ship-stage", run_id, "--stage", "9")
        self.assertIn("0 tới 6", err.getvalue())


if __name__ == "__main__":
    unittest.main()
