import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ctt import hooks
from tests.fixtures import assistant, usage, write_jsonl

OPTS = dict(hooks.DEFAULTS)
NOW = datetime(2026, 11, 11, 3, 10, tzinfo=timezone.utc)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CTT_HOME": self.temp.name})
        self.env.start()
        self.transcript = Path(self.temp.name) / "t.jsonl"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write(self, read, at="2026-11-11T03:05:00Z", sidechain_after=False):
        entries = [assistant("s", "m1", "r1", at, usage=usage(input_tokens=10, read=read, write=100))]
        if sidechain_after:
            side = assistant("s", "m2", "r2", at, usage=usage(read=5))
            side["isSidechain"] = True
            entries.append(side)
        write_jsonl(self.transcript, entries)

    def prompt(self, now=NOW):
        return hooks.user_prompt_submit({"session_id": "s", "transcript_path": str(self.transcript)}, OPTS, now)

    def test_small_recent_session_is_quiet(self):
        self.write(read=50_000)
        self.assertEqual(self.prompt(), {})

    def test_large_context_warns_once_per_step(self):
        self.write(read=250_000)
        first = self.prompt()
        self.assertIn("250k token", first["systemMessage"])
        self.assertIn("/token-tools:handoff", first["systemMessage"])  # points at the hand-over command
        self.assertEqual(self.prompt(), {})          # same 100k step → no repeat
        self.write(read=320_000)
        self.assertIn("Context phiên", self.prompt()["systemMessage"])

    def test_idle_session_warns_rewrite_cost(self):
        self.write(read=50_000, at="2026-11-11T01:00:00Z")
        message = self.prompt()["systemMessage"]
        self.assertIn("nghỉ 130 phút", message)
        self.assertIn("$0.40", message)  # 50_110 tokens × $8/M (opus-5-5 1h write)

    def test_sidechain_entries_ignored(self):
        self.write(read=250_000, sidechain_after=True)
        self.assertIn("250k", self.prompt()["systemMessage"])

    def test_missing_transcript_is_quiet(self):
        self.assertEqual(hooks.user_prompt_submit({}, OPTS, NOW), {})

    def test_post_tool_use(self):
        self.assertEqual(hooks.post_tool_use({"tool_name": "Bash", "tool_response": "ok"}, OPTS), {})
        big = hooks.post_tool_use({"tool_name": "Bash", "tool_response": {"stdout": "x" * 60_000}}, OPTS)
        self.assertIn("Bash", big["systemMessage"])
        self.assertEqual(big["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("lọc", big["hookSpecificOutput"]["additionalContext"])

    def test_file_writing_tools_never_warn(self):
        echoed = {"filePath": "a.html", "originalFile": "x" * 60_000, "structuredPatch": []}
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertEqual(hooks.post_tool_use({"tool_name": tool, "tool_response": echoed}, OPTS), {})

    def test_bash_counts_only_stdout_stderr(self):
        quiet = {"stdout": "ok", "stderr": "", "interrupted": False, "noise": "x" * 60_000}
        self.assertEqual(hooks.post_tool_use({"tool_name": "Bash", "tool_response": quiet}, OPTS), {})
        loud = {"stdout": "x" * 30_000, "stderr": "y" * 1_000}
        self.assertIn("Bash", hooks.post_tool_use({"tool_name": "Bash", "tool_response": loud}, OPTS)["systemMessage"])

    def test_read_counts_file_content(self):
        response = {"type": "text", "file": {"filePath": "big.log", "content": "x" * 30_000}}
        self.assertIn("Read", hooks.post_tool_use({"tool_name": "Read", "tool_response": response}, OPTS)["systemMessage"])

    def run_main(self, args, stdin, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), mock.patch("sys.stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hooks.main(args)
        return code, json.loads(out.getvalue())

    def test_main_switch_off_and_bad_input(self):
        big = json.dumps({"tool_name": "Bash", "tool_response": "x" * 60_000})
        self.assertEqual(self.run_main(["post-tool-use"], big, {"CTT_HOOKS": "off"}), (0, {}))
        self.assertEqual(self.run_main(["post-tool-use"], "{not json"), (0, {}))
        self.assertEqual(self.run_main(["unknown-event"], big), (0, {}))
        code, result = self.run_main(["post-tool-use"], big)
        self.assertEqual(code, 0)
        self.assertIn("systemMessage", result)

    def test_policy_can_disable(self):
        (Path(self.temp.name) / "policy.toml").write_text("[hooks]\nenabled = false\n", encoding="utf-8")
        big = json.dumps({"tool_name": "Bash", "tool_response": "x" * 60_000})
        self.assertEqual(self.run_main(["post-tool-use"], big), (0, {}))


if __name__ == "__main__":
    unittest.main()
