import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt import mcp
from ctt.cli import main

SESSION = "sess-1"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def use(call_id, name, stamp):
    return {"type": "assistant", "sessionId": SESSION, "timestamp": stamp,
            "message": {"id": "m" + call_id, "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}]}}


def result(call_id, text, error=False):
    return {"type": "user", "sessionId": SESSION,
            "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": error,
                                     "content": [{"type": "text", "text": text}]}]}}


class McpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "claude"
        self.project = root / "proj"
        self.projects = root / "projects"
        self.project.mkdir()
        plugin = root / "plugin-install"
        write_json(plugin / ".mcp.json", {"mcpServers": {"postman": {"type": "http", "url": "https://x"}}})
        write_json(self.home / "plugins" / "installed_plugins.json",
                   {"plugins": {"postman@market": [{"installPath": str(plugin)}],
                                "off@market": [{"installPath": str(plugin)}]}})
        write_json(self.home / "settings.json", {"enabledPlugins": {"off@market": False}})
        write_json(self.home / "mcp-needs-auth-cache.json",
                   {"plugin:zapier:zapier": {}, "claude.ai Gmail": {}, "postman": {}})
        write_json(root / "claude.json", {
            "mcpServers": {"mine": {"command": "node"}},
            "projects": {str(self.project).replace("\\", "/"): {
                "mcpServers": {"loc": {"type": "sse", "url": "u"}, "off": {"command": "x"}},
                "disabledMcpServers": ["off"]}}})
        write_json(self.project / ".mcp.json", {"mcpServers": {"shared": {"type": "http", "url": "u"},
                                                                "pending": {"command": "p"}}})
        write_json(self.project / ".claude" / "settings.local.json", {"enabledMcpjsonServers": ["shared"]})
        lines = [use("t1", "mcp__mine__ping", "2026-01-01T00:00:00Z"),
                 result("t1", "x" * 40),
                 use("t2", "mcp__mine__ping", "2026-01-02T00:00:00Z"),
                 result("t2", "boom", error=True),
                 use("t3", "mcp__claude_ai_Gmail__search", "2026-01-03T00:00:00Z"),
                 result("t3", "y" * 8),
                 use("t4", "mcp__plugin_zapier_zapier__run", "2026-01-03T00:00:00Z"),
                 use("t5", "mcp__ccd_session__x", "2026-01-03T00:00:00Z"),
                 use("t6", "mcp__stranger__go", "2026-01-03T00:00:00Z"),
                 use("t1", "mcp__mine__ping", "2026-01-04T00:00:00Z")]  # same id again: counted once
        folder = self.projects / "folder"
        folder.mkdir(parents=True)
        with (folder / f"{SESSION}.jsonl").open("w", encoding="utf-8") as handle:
            for entry in lines:
                handle.write(json.dumps(entry) + "\n")
            handle.write("not json mcp__ tool_use\n")
        sub = folder / SESSION / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_text(json.dumps(use("s1", "mcp__mine__other", "2026-01-05T00:00:00Z")) + "\n",
                                            encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"CTT_CLAUDE_HOME": str(self.home),
                                               "CTT_CLAUDE_JSON": str(root / "claude.json"),
                                               "CTT_HOME": str(root / "ctt"),
                                               "CTT_CLAUDE_PROJECTS": str(self.projects)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def listed(self, session=None):
        return {s["name"]: s for s in mcp.list_servers(self.project, self.projects, session)["servers"]}

    def test_configured_servers_sources_and_status(self):
        found = self.listed()
        self.assertEqual(found["mine"]["source"], "user")
        self.assertEqual(found["mine"]["type"], "stdio")
        self.assertEqual((found["loc"]["source"], found["loc"]["type"]), ("local", "sse"))
        self.assertEqual(found["off"]["status"], "disabled")
        self.assertEqual((found["shared"]["source"], found["shared"]["status"]), ("project", "configured"))
        self.assertEqual(found["plugin:postman:postman"]["source"], "plugin")
        self.assertEqual(found["plugin:postman:postman"]["type"], "http")
        self.assertNotIn("plugin:off:postman", found)
        self.assertEqual(found["plugin:zapier:zapier"]["status"], "needs_auth")
        self.assertEqual((found["claude.ai Gmail"]["source"], found["claude.ai Gmail"]["status"]),
                         ("claudeai", "needs_auth"))
        self.assertEqual(found["mine"]["calls"], 0)
        self.assertNotIn("stranger", found)

    def test_unapproved_project_server(self):
        self.assertEqual(self.listed()["pending"]["status"], "unapproved")
        write_json(self.project / ".claude" / "settings.json", {"enableAllProjectMcpServers": True})
        self.assertEqual(self.listed()["pending"]["status"], "configured")

    def test_plugin_scope_and_project_settings(self):
        other = Path(self.temp.name) / "plugin-install"
        write_json(self.home / "plugins" / "installed_plugins.json", {"plugins": {
            "postman@market": [{"installPath": str(other), "scope": "project", "projectPath": "/elsewhere"}],
            "here@market": [{"installPath": str(other), "scope": "project", "projectPath": str(self.project)}]}})
        found = self.listed()
        self.assertNotIn("plugin:postman:postman", found)
        self.assertIn("plugin:here:postman", found)
        write_json(self.project / ".claude" / "settings.json", {"enabledPlugins": {"here@market": False}})
        self.assertNotIn("plugin:here:postman", self.listed())
        write_json(self.project / ".claude" / "settings.local.json", {"enabledPlugins": {"here@market": True}})
        self.assertIn("plugin:here:postman", self.listed())

    def test_same_prefix_names_keep_first_and_stats(self):
        write_json(Path(self.temp.name) / "claude.json", {"mcpServers": {"a b": {"command": "x"}, "a_b": {"command": "y"}}})
        write_json(self.home / "mcp-needs-auth-cache.json", {})
        folder = self.projects / "folder"
        (folder / "s2.jsonl").write_text(json.dumps({**use("z1", "mcp__a_b__t", "2026-02-01T00:00:00Z"),
                                                     "sessionId": "s2"}) + "\n", encoding="utf-8")
        found = self.listed("s2")
        self.assertEqual(found["a b"]["calls"], 1)
        self.assertEqual(found["a_b"]["calls"], 0)

    def test_session_call_stats_including_subagents(self):
        found = self.listed(SESSION)
        mine = found["mine"]
        self.assertEqual((mine["calls"], mine["errors"]), (3, 1))
        self.assertEqual(mine["tools"], {"ping": 2, "other": 1})
        self.assertEqual(mine["result_tokens"], 11)  # (40 + 4) chars / 4
        self.assertEqual(mine["last_at"], "2026-01-05T00:00:00Z")
        self.assertEqual(found["claude.ai Gmail"]["calls"], 1)       # claude_ai_Gmail maps back to the listed name
        self.assertEqual(found["plugin:zapier:zapier"]["calls"], 1)  # plugin_zapier_zapier too
        self.assertEqual(found["stranger"]["source"], "unknown")
        self.assertFalse([name for name in found if name.startswith("ccd_")])

    def test_broken_files_and_unknown_session_do_not_crash(self):
        (self.home / "mcp-needs-auth-cache.json").write_text("{oops", encoding="utf-8")
        (self.project / ".mcp.json").write_text("[]", encoding="utf-8")
        data = mcp.list_servers(self.project, self.projects, "missing")
        self.assertNotIn("error", data)
        self.assertIn("mine", {s["name"] for s in data["servers"]})

    def test_cli_json_and_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["mcp", "--cwd", str(self.project), "--session", SESSION, "--json"])
        self.assertEqual(json.loads(out.getvalue())["servers"][0]["name"], "mine")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["mcp", "--cwd", str(self.project)])
        self.assertIn("needs_auth", out.getvalue())


if __name__ == "__main__":
    unittest.main()
