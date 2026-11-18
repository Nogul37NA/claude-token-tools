import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt import live
from ctt.estimate import ledger
from ctt.pricing import PriceTable
from tests.fixtures import PRICES, assistant, usage, write_jsonl

TABLE = PriceTable(PRICES)


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CTT_HOME": str(Path(self.temp.name) / "home")})
        self.env.start()
        self.projects = Path(self.temp.name) / "projects"
        folder = self.projects / "C--work-demo"
        self.main = folder / "s1.jsonl"
        write_jsonl(self.main, [assistant("s1", "m1", "r1", "2026-11-12T01:00:00Z", usage=usage(read=1000, output=10))])
        side = assistant("s1", "m2", "r2", "2026-11-12T01:01:00Z", model="claude-sonnet-5-5", usage=usage(read=500))
        side["isSidechain"] = True
        self.agent_file = folder / "s1" / "subagents" / "agent-abc.jsonl"
        write_jsonl(self.agent_file, [side])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_main_and_subagent_counted_separately(self):
        result = live.agent_usage(self.projects, "s1", TABLE)
        self.assertEqual(result["agents"]["main"]["tokens"], 1010)
        self.assertEqual(result["agents"]["abc"]["tokens"], 500)    # sidechain lines count for subagents
        self.assertEqual(result["total"]["tokens"], 1510)
        self.assertIsNone(result["budget"])

    def test_second_call_reads_only_new_lines(self):
        live.agent_usage(self.projects, "s1", TABLE)
        with self.main.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant("s1", "m3", "r3", "2026-11-12T01:02:00Z", usage=usage(read=200))) + "\n")
        result = live.agent_usage(self.projects, "s1", TABLE)
        self.assertEqual(result["agents"]["main"]["tokens"], 1210)
        self.assertEqual(result["agents"]["abc"]["tokens"], 500)    # not counted twice

    def test_tokens_split_into_new_and_cache_reread(self):
        with self.main.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant("s1", "m3", "r3", "2026-11-12T01:02:00Z",
                                              usage=usage(input_tokens=5, read=0, write=85, output=0))) + "\n")
        main = live.agent_usage(self.projects, "s1", TABLE)["agents"]["main"]
        self.assertEqual((main["input"], main["output"], main["cache_read"], main["cache_write"]), (5, 10, 1000, 85))
        self.assertEqual((main["tokens"], main["new"]), (1100, 100))
        self.assertAlmostEqual(main["cache_read_pct"], 90.9)
        total = live.agent_usage(self.projects, "s1", TABLE)["total"]
        self.assertEqual((total["tokens"], total["cache_read"], total["new"]), (1600, 1500, 100))

    def test_state_from_before_the_split_is_read_again(self):
        state_dir = Path(self.temp.name) / "home" / "live_state"
        state_dir.mkdir(parents=True)
        (state_dir / "s1.json").write_text(json.dumps(
            {"main": {"offset": self.main.stat().st_size, "last_key": None, "tokens": 7, "usd": 0.0}}), encoding="utf-8")
        main = live.agent_usage(self.projects, "s1", TABLE)["agents"]["main"]
        self.assertEqual((main["tokens"], main["cache_read"]), (1010, 1000))

    def test_unknown_session_is_empty(self):
        self.assertEqual(live.agent_usage(self.projects, "nope", TABLE)["agents"], {})

    def test_budget_of_approved_estimate(self):
        est = {"id": "e1", "title": "T", "tokens": {"p50": 10, "p90": 20, "budget": 24},
               "parts": [{"steps": [{"role": "developer", "share": 1.0}]}]}
        ledger.append({"event": "created", "estimate": est, "session_id": "s1", "transcript_path": str(self.main),
                       "start_offset": 0})
        self.assertIsNone(live.agent_usage(self.projects, "s1", TABLE)["budget"])  # not approved yet
        ledger.append({"event": "approved", "id": "e1", "at": "2026-11-12T01:00:00Z"})
        budget = live.agent_usage(self.projects, "s1", TABLE)["budget"]
        self.assertEqual((budget["id"], budget["budget_tokens"], budget["used_tokens"]), ("e1", 24, 0))


class LongPathTest(unittest.TestCase):
    """0.22.2: a session opened without a folder has a project folder name of ~190 characters, so on Windows its
    subagent transcripts lie past MAX_PATH (260) and were neither listed nor read."""

    def test_long_path_form(self):
        import os
        from ctt import config
        if os.name != "nt":
            self.assertEqual(config.long_path("/tmp/x"), Path("/tmp/x"))
            return
        self.assertEqual(str(config.long_path("C:\\work\\demo")), "\\\\?\\C:\\work\\demo")
        self.assertEqual(str(config.long_path("\\\\?\\C:\\work\\demo")), "\\\\?\\C:\\work\\demo")  # not prefixed twice
        self.assertEqual(str(config.long_path("\\\\server\\share\\x")), "\\\\?\\UNC\\server\\share\\x")

    def test_subagent_transcripts_past_260_characters_are_found_and_counted(self):
        import os
        import tempfile
        from ctt import config, live
        from ctt.pricing import PriceTable
        from tests.fixtures import PRICES, assistant, usage, write_jsonl
        with tempfile.TemporaryDirectory() as temp:
            projects = Path(temp) / "projects"
            folder = "C--" + "-".join(["scratch-workspaces"] * 12)  # ~230 characters, as the app's own are long
            session = "11111111-2222-3333-4444-555555555555"
            base = config.long_path(projects / folder)
            write_jsonl(base / f"{session}.jsonl", [assistant(session, "m0", "r0", "2026-11-12T01:00:00Z",
                                                              usage=usage(read=1000))])
            agent = base / session / "subagents" / "agent-a1b2c3.jsonl"
            write_jsonl(agent, [assistant(session, "m1", "r1", "2026-11-12T01:01:00Z", usage=usage(read=500))])
            if os.name == "nt":
                self.assertGreater(len(str(projects / folder / session / "subagents" / "agent-a1b2c3.jsonl")), 260)
            files = live.session_files(projects, session)  # the caller's plain path, as the CLI passes it
            self.assertEqual(sorted(files), ["a1b2c3", "main"])
            used = live.agent_usage(projects, session, PriceTable(PRICES), state_dir=Path(temp) / "state")
            self.assertEqual(used["agents"]["a1b2c3"]["tokens"], 500)
            self.assertEqual(used["total"]["tokens"], 1500)
            import shutil
            shutil.rmtree(config.long_path(projects))  # the temp folder's own cleanup cannot remove long paths


if __name__ == "__main__":
    unittest.main()
