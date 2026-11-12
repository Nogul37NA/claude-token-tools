import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ctt import ship, ship_report

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


class Prices:
    def cost(self, request):
        return request.total / 1e6, True


def request(session, minutes, tokens):
    return SimpleNamespace(session_id=session, timestamp=NOW - timedelta(minutes=minutes), total=tokens)


class ShipReportTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"CTT_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.projects = Path(self.home.name, "projects")
        self.projects.mkdir()
        self.store = Path(self.home.name, "store")
        self.store.mkdir()

    def make_run(self, title, session, minutes_ago, result=None, plan=None, estimate=None):
        at = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        run = ship.start(title, session, "C:/w", now=at)
        if plan:
            run = ship.set_stage(run["id"], 3, plan=plan, limits={**ship.DEFAULTS, "max_children": 4}, now=at,
                                 estimate_id=estimate)
        if result:
            run = ship.finish(run["id"], result, now=(NOW - timedelta(minutes=minutes_ago - 30)).isoformat())
        return run

    def test_collect_counts_children_finds_session_and_writes_page(self):
        plan = [{"name": "Core", "role": "developer", "mode": "session", "tokens": 3e6},
                {"name": "Test", "role": "tester", "mode": "subagent"}]
        done = self.make_run("Việc A", "s-1", 60, "ok", plan)
        lost = self.make_run("Việc B", None, 50)
        self.make_run("Cũ", "s-old", 60 * 24 * 30, "ok")
        (self.store / "token-tools_x.json").write_text(json.dumps(
            {"session:k-1": {"id": "k-1", "parent": "s-1"}, "session:s-1": {"id": "s-1"}}), encoding="utf-8")
        scan = SimpleNamespace(
            requests=[request("s-1", 50, 1_000_000), request("k-1", 40, 2_000_000), request("s-1", 5, 9_000_000),
                      request("s-2", 45, 500_000)],
            tool_uses=[SimpleNamespace(session_id="s-2", input={"command": f"ctt ship-stage {lost['id']} --stage 1"})])
        report = ship_report.collect(14, self.projects, Prices(), scan, self.store, now=NOW)
        runs = {r["title"]: r for r in report["runs"]}
        self.assertEqual(set(runs), {"Việc A", "Việc B"})  # older than 14 days left out
        a = runs["Việc A"]
        self.assertEqual((a["children"], a["tokens"], a["modes"]["session"], a["result"]), (["k-1"], 3_000_000, 1, "ok"))
        self.assertEqual(ship.load(done["id"])["children"], ["k-1"])  # kept on the run
        self.assertEqual(ship.load(lost["id"])["session_id"], "s-2")  # found from `ship-stage <id>`
        self.assertEqual(runs["Việc B"]["tokens"], 500_000)
        t = report["totals"]
        self.assertEqual((t["runs"], t["done"], t["ok"]), (2, 1, 1))
        path = ship_report.write(report, Path(self.home.name, "out"))
        page = path.read_text(encoding="utf-8")
        for piece in ("<title>Báo cáo ship</title>", "Việc A", "phiên con", "Phiếu so với thực tế", "prefers-color-scheme",
                      "Chi phí từng lượt", 'data-tip="Việc A', "Phiên chính $1.00 · 1 phiên con $2.00", "<script>"):
            self.assertIn(piece, page)
        self.assertIn("Lượt ship 14 ngày qua: 2 lượt, 1 đã xong", ship_report.text(report))

    def test_advice_from_ratios_and_refusals(self):
        runs = [{"ratio": 2.0, "within_p90": False, "failed_steps": [], "tokens": 1, "children": [], "usd": 1, "roles": {"tester": 3}, "refused": {"chế độ Auto chặn": 2},
                 "result": "ok", "modes": dict.fromkeys(ship.MODES, 0), "minutes": 10} for _ in range(3)]
        tips = " | ".join(ship_report.advice(runs))
        self.assertIn("token-calibrate", tips)
        self.assertIn("chế độ Auto chặn", tips)
        self.assertIn("Vai tester bị chặn 9 lần", tips)

    def test_friction_reads_role_refusals_and_failed_starts(self):
        folder = self.projects / "C--w"
        folder.mkdir()
        lines = [
            {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__ccd_session__start_session"}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                      "content": "Session not started: Blocked by auto mode"}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                      "content": "Vai tester không ghi ngoài thư mục project"}]}},
        ]
        (folder / "s-9.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        roles, refused = ship_report.friction(self.projects, ["s-9"])
        self.assertEqual((roles, refused), ({"tester": 1}, {"chế độ Auto chặn": 1}))


if __name__ == "__main__":
    unittest.main()
