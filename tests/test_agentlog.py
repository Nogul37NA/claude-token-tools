import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ctt import agentlog
from ctt.cli import main


def row(kind, content):
    return {"type": kind, "message": {"role": kind, "content": content}}


AGENT_ROWS = [
    row("user", "Chạy mvn -q compile rồi báo kết quả."),
    row("user", "<system-reminder>\nnội bộ, không hiện\n</system-reminder>"),
    {"type": "attachment"},
    row("assistant", [{"type": "text", "text": "Tôi chạy lệnh."},
                      {"type": "tool_use", "id": "t1", "name": "PowerShell", "input": {"command": "mvn -q compile"}}]),
    row("user", [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                  "content": "\nPermission denied: Vai reviewer không được chạy\nchi tiết"}]),
    row("assistant", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "C:/r/pom.xml"}}]),
    row("user", [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "<project>"}]}]),
    row("assistant", [{"type": "tool_use", "id": "t3", "name": "SubagentHandback",
                       "input": {"message": "Bị chặn, không chạy được."}}]),
    row("user", [{"type": "tool_result", "tool_use_id": "t3", "content": "delivered"}]),
]


class AgentLogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name)
        folder = self.projects / "C--repo"
        (folder / "s1" / "subagents").mkdir(parents=True)
        (folder / "s1.jsonl").write_text("", encoding="utf-8")
        self.agent = folder / "s1" / "subagents" / "agent-a1.jsonl"
        self.agent.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in AGENT_ROWS) + "{broken\n",
                              encoding="utf-8")

    def test_lines_in_order_with_errors_marked(self):
        lines = agentlog.agent_log(self.projects, "s1", "a1")["lines"]
        self.assertEqual([(l["kind"], l["text"], l["error"]) for l in lines], [
            ("task", "Chạy mvn -q compile rồi báo kết quả.", False),
            ("say", "Tôi chạy lệnh.", False),
            ("call", "PowerShell mvn -q compile", False),
            ("result", "Permission denied: Vai reviewer không được chạy", True),
            ("call", "Read C:/r/pom.xml", False),
            ("result", "<project>", False),
            ("answer", "Bị chặn, không chạy được.", False),
        ])

    def test_last_and_unknown_agent(self):
        self.assertEqual(len(agentlog.agent_log(self.projects, "s1", "a1", last=2)["lines"]), 2)
        self.assertIn("error", agentlog.agent_log(self.projects, "s1", "zz"))

    def test_only_the_tail_is_read(self):
        tail = sum(len((json.dumps(r, ensure_ascii=False) + "\n").encode()) for r in AGENT_ROWS[-4:]) + 20
        lines = agentlog.entries(self.agent, tail_bytes=tail)
        self.assertNotIn("task", [line["kind"] for line in lines])  # the start is beyond the tail
        self.assertEqual(lines[-1]["kind"], "answer")

    def test_internal_lines_are_left_out(self):
        noisy = self.agent.parent / "agent-a2.jsonl"
        rows = [
            row("user", "Giao việc."),
            row("assistant", [{"type": "tool_use", "id": "x1", "name": "Agent", "input": {"description": "Tạo file"}}]),
            row("user", [{"type": "tool_result", "tool_use_id": "x1",
                          "content": "Async agent launched successfully. (internal metadata)"}]),
            row("user", 'Another Claude session sent a message: <agent-message from="a">…'),
        ]
        noisy.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual([(l["kind"], l["text"]) for l in agentlog.entries(noisy)],
                         [("task", "Giao việc."), ("call", "Agent Tạo file")])

    def test_cli_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["agent-log", "--session", "s1", "--agent", "a1", "--projects-dir", str(self.projects)])
        self.assertEqual(json.loads(out.getvalue())["lines"][-1]["kind"], "answer")


if __name__ == "__main__":
    unittest.main()
