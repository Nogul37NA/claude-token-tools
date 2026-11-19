import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ctt import cli, live, race
from ctt.pricing import PriceTable
from tests.fixtures import PRICES, assistant, usage, write_jsonl

TABLE = PriceTable(PRICES)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cwd), *args],
                   check=True, capture_output=True)


class RaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"CTT_HOME": str(root / "home")})
        self.env.start()
        self.projects = root / "projects"
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "race-a")
        (self.repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("new\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "work")
        git(self.repo, "checkout", "-q", "-")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def new(self, entrants="sonnet:medium,opus:high", session="s1"):
        return race.start("Thêm API", "làm API", race.parse_entrants(entrants), session, str(self.repo))

    def agent(self, agent_id, model, **usage_kwargs):
        line = assistant("s1", f"m-{agent_id}", f"r-{agent_id}", "2026-11-12T01:01:00Z", model=model,
                         usage=usage(**usage_kwargs))
        line["isSidechain"] = True
        write_jsonl(self.projects / "C--work-demo" / "s1" / "subagents" / f"agent-{agent_id}.jsonl", [line])
        main = self.projects / "C--work-demo" / "s1.jsonl"
        if not main.exists():
            write_jsonl(main, [assistant("s1", "m1", "r1", "2026-11-12T01:00:00Z", usage=usage(output=1))])

    def scored(self, review_a, review_b, usd_a=None, usd_b=None, fail_a=0, fail_b=0):
        r = self.new()
        for name, review, usd, fail in (("A", review_a, usd_a, fail_a), ("B", review_b, usd_b, fail_b)):
            race.score(r["id"], name, tests_pass=5, tests_fail=fail, review=review, notes="ok")
            data = race.load(r["id"])
            data["entrants"][ord(name) - 65]["usd"] = usd
            race.save(data)
        return race.load(r["id"])

    def test_start_validation(self):
        self.assertEqual(race.parse_entrants("sonnet:medium, opus:high"), [("sonnet", "medium"), ("opus", "high")])
        for bad in ("sonnet:medium", "sonnet:medium,gpt:high", "sonnet:medium,opus:ultra", "sonnet,opus:high",
                    "haiku:low,sonnet:low,opus:low,fable:low"):
            with self.assertRaises(ValueError, msg=bad):
                race.parse_entrants(bad)
        r = self.new("haiku:low,sonnet:high,opus:max")
        self.assertEqual([e["name"] for e in r["entrants"]], ["A", "B", "C"])
        self.assertEqual(r["status"], "running")
        self.assertEqual(r["base"], race.head_sha(self.repo))
        self.assertEqual(len(r["base"]), 40)
        self.assertTrue(r["id"].endswith("them-api"))
        self.assertEqual(self.new()["id"], r["id"] + "-2")
        self.assertEqual(race.settings({})["entrants"], ["sonnet:medium", "opus:high"])
        with self.assertRaises(ValueError):
            race.settings({"race": {"max_entrants": 5}})
        plain = race.start("x", "t", [("haiku", "low"), ("opus", "low")], None, str(Path(self.temp.name)))
        self.assertIsNone(plain["base"])

    def test_default_policy_has_race_section(self):
        from ctt.policy import load_policy
        self.assertEqual(race.settings(load_policy(None))["max_entrants"], 3)

    def test_entry_and_score(self):
        r = self.new()
        race.set_entry(r["id"], "A", agent_id="ag1", worktree="/w/a", branch="race-a", status="running")
        data = race.load(r["id"])
        self.assertEqual((data["entrants"][0]["agent_id"], data["entrants"][0]["status"]), ("ag1", "running"))
        with self.assertRaises(ValueError):
            race.set_entry(r["id"], "C")
        with self.assertRaises(ValueError):
            race.set_entry(r["id"], "A", status="weird")
        self.assertEqual(data["status"], "running")
        race.score(r["id"], "A", tests_pass=4)
        self.assertEqual(race.load(r["id"])["status"], "judging")
        race.score(r["id"], "A", tests_fail=1, review=7.5, notes="gọn")
        entrant = race.load(r["id"])["entrants"][0]
        self.assertEqual(entrant["tests"], {"pass": 4, "fail": 1})
        self.assertEqual(entrant["review"], {"score": 7.5, "notes": "gọn"})
        with self.assertRaises(ValueError):
            race.score(r["id"], "A", review=11)

    def test_suggestion_best_score_tie_cheaper_and_test_penalty(self):
        clear = self.scored(9, 6, 5.0, 1.0)
        self.assertEqual(race.suggest(clear)[0], "A")
        tie = self.scored(8.5, 8.0, 5.0, 1.0)  # within 1.0 → cheaper B
        name, reason = race.suggest(tie)
        self.assertEqual(name, "B")
        self.assertIn("rẻ", reason)
        edge = self.scored(9.5, 8.0, 5.0, 1.0)  # 1.5 apart: not a tie
        self.assertEqual(race.suggest(edge)[0], "A")
        penalised = self.scored(9, 7, 5.0, 1.0, fail_a=1)  # A: 9-3=6 < B 7
        self.assertEqual(race.quality(penalised["entrants"][0]), 6)
        self.assertEqual(race.suggest(penalised)[0], "B")
        self.assertEqual(race.suggest(self.new())[0], None)
        self.assertIsNone(race.quality(self.new()["entrants"][0]))

    def test_report_fills_figures_and_diff(self):
        r = self.new(session="s1")
        self.agent("ag1", "claude-sonnet-5-5", output=1000, read=2000)
        self.agent("ag2", "claude-opus-5-5", output=500)
        race.set_entry(r["id"], "A", agent_id="ag1", worktree=str(self.repo), branch="race-a", status="done")
        race.set_entry(r["id"], "B", agent_id="ag2", worktree=str(self.repo), status="done")
        race.score(r["id"], "A", tests_pass=3, tests_fail=0, review=8)
        race.score(r["id"], "B", tests_pass=3, tests_fail=0, review=8.5)
        result = race.report(r["id"], self.projects, TABLE)
        a, b = result["rows"]
        self.assertEqual(a["tokens"], 3000)
        self.assertAlmostEqual(a["usd"], 1000 * 10 / 1e6 + 2000 * 0.1 / 1e6, places=6)
        self.assertEqual(b["tokens"], 500)
        self.assertEqual(a["diff"], {"files": 2, "insertions": 3, "deletions": 0})
        self.assertEqual(b["diff"], {"files": 0, "insertions": 0, "deletions": 0})  # worktree == repo, no change
        self.assertEqual(result["suggested"], "B")  # tie (0.5) → B is cheaper
        self.assertEqual(race.load(r["id"])["entrants"][0]["tokens"], 3000)  # saved back
        text = race.report_text(result)
        self.assertIn("Đối thủ | Model", text)
        self.assertIn("Đề xuất: B", text)

    def test_report_tolerates_missing_transcript_and_git(self):
        r = race.start("x", "t", [("haiku", "low"), ("opus", "low")], "nope", str(Path(self.temp.name)))
        race.set_entry(r["id"], "A", agent_id="ghost", branch="b")
        result = race.report(r["id"], self.projects, TABLE)
        self.assertIsNone(result["rows"][0]["tokens"])
        self.assertIsNone(result["rows"][0]["diff"])
        self.assertIsNone(result["suggested"])
        self.assertIn("Chưa đề xuất", race.report_text(result))

    def test_pick_ledger_and_stats(self):
        r = self.scored(9, 6, 4.0, 1.0)
        race.pick(r["id"], "A", note="gọn hơn")
        data = race.load(r["id"])
        self.assertEqual((data["status"], data["winner"]), ("picked", "A"))
        lines = race.picks_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["winner"], {"model": "sonnet", "effort": "medium"})
        self.assertEqual(record["entrants"][1]["quality"], 6)
        with self.assertRaises(ValueError):
            race.pick(r["id"], "B")
        r2 = self.scored(5, 9, 2.0, 6.0)
        race.pick(r2["id"], "B")
        rows = {row["entrant"]: row for row in race.stats()}
        self.assertEqual(rows["sonnet:medium"]["races"], 2)
        self.assertEqual(rows["sonnet:medium"]["wins"], 1)
        self.assertEqual(rows["sonnet:medium"]["win_rate"], 0.5)
        self.assertEqual(rows["sonnet:medium"]["median_usd"], 3.0)
        self.assertEqual(rows["opus:high"]["median_quality"], 7.5)
        self.assertEqual(race.stats(days=0), [])
        cancelled = self.new()
        race.cancel(cancelled["id"], "thôi")
        self.assertEqual(race.load(cancelled["id"])["status"], "cancelled")
        with self.assertRaises(ValueError):
            race.score(cancelled["id"], "A", review=5)

    def test_for_session_and_agent_usage_include_race(self):
        self.agent("ag1", "claude-sonnet-5-5", output=100)
        self.assertIsNone(live.agent_usage(self.projects, "s1", TABLE)["race"])
        r = self.new(session="s1")
        self.assertEqual(race.for_session("s1")["id"], r["id"])
        self.assertIsNone(race.for_session("other"))
        race.score(r["id"], "A", tests_pass=2, tests_fail=0, review=8)
        brief = live.agent_usage(self.projects, "s1", TABLE)["race"]
        self.assertEqual(set(brief), {"id", "title", "status", "entrants", "winner", "suggested"})
        self.assertEqual(brief["status"], "judging")
        self.assertEqual(brief["suggested"], "A")
        self.assertEqual(set(brief["entrants"][0]), {"name", "model", "effort", "status", "tokens", "usd",
                                                     "quality", "tests_pass", "tests_fail"})
        self.assertEqual(brief["entrants"][0]["quality"], 8)
        race.set_entry(r["id"], "A", agent_id="agent-ag1")
        running = live.agent_usage(self.projects, "s1", TABLE)["race"]["entrants"][0]
        self.assertEqual(running["tokens"], 100)  # live spend from the agent's transcript, before any report
        self.assertGreater(running["usd"], 0)
        race.pick(r["id"], "A")
        self.assertEqual(live.agent_usage(self.projects, "s1", TABLE)["race"]["winner"], "A")  # just picked
        data = race.load(r["id"])
        data["picked_at"] = "2026-01-01T00:00:00+00:00"
        race.save(data)
        self.assertIsNone(live.agent_usage(self.projects, "s1", TABLE)["race"])

    def test_diff_needs_worktree_and_falls_back_to_base(self):
        r = self.new()
        race.set_entry(r["id"], "A", branch="race-a")  # no worktree: never the main checkout
        data = race.load(r["id"])
        self.assertIsNone(race.diff_of(data, data["entrants"][0]))
        (self.repo / "a.txt").write_text("one\nlocal\n", encoding="utf-8")  # uncommitted, tracked
        entrant = {"worktree": str(self.repo), "branch": "no-such-branch"}
        self.assertEqual(race.diff_of(data, entrant), {"files": 1, "insertions": 1, "deletions": 0})

    def test_start_refuses_non_repo_and_dirty_tree(self):
        def start(cwd):
            with contextlib.redirect_stderr(io.StringIO()):
                return self.run_cli("race-start", "--title", "T", "--task", "t", "--cwd", str(cwd),
                                    "--session", "none")
        with self.assertRaises(SystemExit) as caught:
            start(self.temp.name)
        self.assertNotEqual(caught.exception.code, 0)
        (self.repo / "a.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            start(self.repo)
        with self.assertRaisesRegex(ValueError, "chưa commit"):
            race.check_clean(self.repo)
        git(self.repo, "checkout", "--", "a.txt")
        race.check_clean(self.repo)
        self.assertIn('"race"', start(self.repo))

    def test_agent_id_prefix_stripped(self):
        r = self.new()
        race.set_entry(r["id"], "A", agent_id="agent-abc123")
        self.assertEqual(race.load(r["id"])["entrants"][0]["agent_id"], "abc123")

    def test_duplicate_entrants_and_stats_tolerate_bad_lines(self):
        with self.assertRaises(ValueError):
            race.parse_entrants("sonnet:high,opus:high,sonnet:high")
        race.races_dir().mkdir(parents=True, exist_ok=True)
        good = {"race": "r", "title": "t", "at": "2999-01-01T00:00:00", "winner": {"model": "opus", "effort": "high"},
                "entrants": [{"model": "opus", "effort": "high", "quality": 8, "usd": 1.0}]}  # naive time → UTC
        lines = [json.dumps(good), json.dumps({"at": "nonsense"}), json.dumps({"title": "x"}),
                 json.dumps({"at": "2999-01-01T00:00:00Z", "entrants": [{"quality": 1}]}), "{broken", '"str"']
        race.picks_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows = race.stats(days=36500 * 2)
        self.assertEqual([(r["entrant"], r["races"], r["wins"]) for r in rows], [("opus:high", 1, 1)])

    def test_recent_ended_race_shown_only_within_ten_minutes(self):
        r = self.new()
        race.cancel(r["id"])
        self.assertEqual(race.for_session("s1")["status"], "cancelled")
        later = datetime.now(timezone.utc) + timedelta(minutes=11)
        self.assertIsNone(race.for_session("s1", now=later))
        active = self.new()
        self.assertEqual(race.for_session("s1")["id"], active["id"])  # an active race wins over an ended one

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_cli_flow(self):
        out = json.loads(self.run_cli("race-start", "--title", "CLI", "--task", "làm", "--cwd", str(self.repo),
                                      "--session", "none", "--entrants", "haiku:low,sonnet:high"))
        rid = out["race"]["id"]
        self.assertIn("A · model haiku", out["brief"])
        self.run_cli("race-entry", rid, "--name", "A", "--status", "done")
        self.run_cli("race-score", rid, "--name", "A", "--review", "8", "--tests-pass", "2")
        self.assertIn("Đề xuất: A", self.run_cli("race-report", rid, "--projects-dir", str(self.projects)))
        report = json.loads(self.run_cli("race-report", rid, "--json", "--projects-dir", str(self.projects)))
        self.assertEqual(report["suggested"], "A")
        self.run_cli("race-pick", rid, "--winner", "A")
        self.assertEqual(json.loads(self.run_cli("race-stats", "--json"))[0]["entrant"], "haiku:low")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("race-start", "--title", "x", "--task", "y", "--session", "none", "--entrants", "opus:high")


if __name__ == "__main__":
    unittest.main()
