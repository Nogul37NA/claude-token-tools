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
        ship.set_step(run["id"], "API", "running")
        brief = ship.brief(ship.for_session("s-1"))
        self.assertEqual((brief["stage"], brief["stage_name"], brief["steps"], brief["running"]),
                         (3, "Chạy", 2, ["API"]))
        ship.set_step(run["id"], "API", "done")
        done = ship.finish(run["id"], "ok", check={"p50": 4e6, "p90": 9e6, "actual": 5e6, "ratio": 1.25,
                                                   "within_p90": True, "title": "x"})
        self.assertEqual((done["stage"], done["result"], done["check"]["ratio"]), (6, "ok", 1.25))
        self.assertIn("×1.25", ship.summary(done))
        with self.assertRaisesRegex(ValueError, "không có bước"):
            ship.set_step(run["id"], "Ghost", "done")
        with self.assertRaises(ValueError):
            ship.load("../evil")
        self.assertIsNone(ship.for_session("nobody"))

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
