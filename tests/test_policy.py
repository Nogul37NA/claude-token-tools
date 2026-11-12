import json
import tempfile
import unittest
from pathlib import Path

from ctt.config import deep_merge
from ctt.policy import rewrite_frontmatter, sync, target_for

POLICY = {
    "session": {"default": "opus"},
    "roles": {"developer": {"model": "sonnet", "effort": "medium"}, "planner": {"model": "opus", "effort": "high"}},
    "agent_role": {"code-simplifier": "developer"},
    "scan": {"roots": [], "include_user_agents": False},
    "projects": {"demo": {"roles": {"developer": {"effort": "high"}}}, "other": {"session": {"default": "sonnet"}}},
}
AGENT = "---\nname: developer\ndescription: Writes code.\n---\n\n# Body\nkeep me\n"


class FrontmatterTest(unittest.TestCase):
    def test_inserts_after_description(self):
        updated = rewrite_frontmatter(AGENT, {"model": "sonnet", "effort": "low"})
        self.assertEqual(updated, "---\nname: developer\ndescription: Writes code.\nmodel: sonnet\neffort: low\n---\n"
                                  "\n# Body\nkeep me\n")

    def test_replaces_existing_and_is_idempotent(self):
        once = rewrite_frontmatter(AGENT.replace("description", "model: opus\ndescription"), {"model": "sonnet"})
        self.assertEqual(once.count("model:"), 1)
        self.assertEqual(rewrite_frontmatter(once, {"model": "sonnet"}), once)

    def test_keeps_crlf(self):
        updated = rewrite_frontmatter(AGENT.replace("\n", "\r\n"), {"model": "opus"})
        self.assertNotIn("\n", updated.replace("\r\n", ""))

    def test_no_frontmatter(self):
        self.assertIsNone(rewrite_frontmatter("# no header\n", {"model": "opus"}))
        self.assertIsNone(rewrite_frontmatter("---\nname: x\n", {"model": "opus"}))

    def test_target_with_project_override(self):
        self.assertEqual(target_for(POLICY, "demo", "code-simplifier"), ("developer", {"model": "sonnet", "effort": "high"}))
        self.assertEqual(target_for(POLICY, "x", "planner"), ("planner", {"model": "opus", "effort": "high"}))
        self.assertEqual(target_for(POLICY, "x", "unknown"), (None, None))

    def test_deep_merge(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}, "l": [1]}, {"a": {"c": 3}, "l": [2]})
        self.assertEqual(merged, {"a": {"b": 1, "c": 3}, "l": [2]})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        agents = root / "projects" / "demo" / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "developer.md").write_text(AGENT, encoding="utf-8")
        (agents / "unmapped.md").write_text(AGENT, encoding="utf-8")
        (root / "projects" / "other").mkdir()
        self.user_settings = root / "settings.json"
        self.user_settings.write_text(json.dumps({"theme": "dark", "statusLine": {"type": "command"}}, indent=2),
                                      encoding="utf-8")
        self.policy = deep_merge(POLICY, {"scan": {"roots": [str(root / "projects")]}})
        self.root = root

    def tearDown(self):
        self.temp.cleanup()

    def run_sync(self, apply):
        return sync(self.policy, apply=apply, user_settings=self.user_settings, out=lambda *_: None)

    def test_dry_run_writes_nothing(self):
        before = self.user_settings.read_text(encoding="utf-8")
        result = self.run_sync(apply=False)
        self.assertEqual((result.agents_changed, result.agents_skipped, result.settings_changed), (1, 1, 2))
        self.assertEqual(self.user_settings.read_text(encoding="utf-8"), before)
        self.assertFalse((self.root / "projects" / "other" / ".claude").exists())

    def test_apply_then_idempotent(self):
        self.run_sync(apply=True)
        settings = json.loads(self.user_settings.read_text(encoding="utf-8"))
        self.assertEqual(settings, {"theme": "dark", "statusLine": {"type": "command"}, "model": "opus"})
        other = json.loads((self.root / "projects" / "other" / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(other, {"model": "sonnet"})
        agent = (self.root / "projects" / "demo" / ".claude" / "agents" / "developer.md").read_text(encoding="utf-8")
        self.assertIn("model: sonnet\neffort: high\n", agent)
        self.assertTrue(agent.endswith("# Body\nkeep me\n"))
        again = self.run_sync(apply=False)
        self.assertEqual((again.agents_changed, again.settings_changed, again.agents_unchanged), (0, 0, 1))

    def test_agents_only_leaves_settings(self):
        before = self.user_settings.read_text(encoding="utf-8")
        result = sync(self.policy, apply=True, user_settings=self.user_settings, out=lambda *_: None, agents_only=True)
        self.assertEqual((result.agents_changed, result.settings_changed), (1, 0))
        self.assertEqual(self.user_settings.read_text(encoding="utf-8"), before)
        self.assertFalse((self.root / "projects" / "other" / ".claude").exists())

    def test_no_roots_uses_current_folder(self):
        policy = deep_merge(POLICY, {"session": {"default": ""}})
        result = sync(policy, cwd=self.root / "projects" / "demo", user_settings=self.user_settings,
                      out=lambda *_: None)
        self.assertEqual(result.agents_changed, 1)


if __name__ == "__main__":
    unittest.main()
