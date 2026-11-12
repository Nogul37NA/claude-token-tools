import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt import agents
from ctt.cli import main
from ctt.config import REPO_ROOT
from ctt.policy import load_policy

ROLES = ("planner", "architect", "research", "developer", "debugger", "tester", "reviewer", "docs")


def agent_file(folder, name, model="sonnet"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(f"---\nname: {name}\ndescription: x\nmodel: {model}\n---\n\nBody\n",
                                      encoding="utf-8")


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "proj"
        self.user = root / "user-agents"
        self.plugin = root / "plugin-agents"
        agent_file(self.project / ".claude" / "agents", "tester")
        agent_file(self.project / ".claude" / "agents", "code-reviewer", "opus")
        agent_file(self.user, "tester", "haiku")      # replaced by the project's tester
        agent_file(self.user, "helper")               # no role
        agent_file(self.plugin, "tester")
        patcher = mock.patch.dict(os.environ, {"CTT_HOME": str(root / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def found(self):
        return {(a["call"], a["level"]): a for a in
                agents.list_agents(self.project, load_policy(), self.user, self.plugin)}

    def test_levels_roles_and_the_project_replacing_the_user(self):
        found = self.found()
        self.assertEqual(set(found), {("tester", "project"), ("code-reviewer", "project"), ("helper", "user"),
                                      ("token-tools:tester", "plugin")})
        self.assertEqual(found[("code-reviewer", "project")]["role"], "reviewer")
        self.assertIsNone(found[("helper", "user")]["role"])
        self.assertEqual(found[("token-tools:tester", "plugin")]["role"], "tester")

    def test_the_plugins_agents_follow_the_policy_models(self):
        policy = load_policy()
        shipped = {path.stem: agents.frontmatter(path) for path in (REPO_ROOT / "agents").glob("*.md")}
        self.assertEqual(set(shipped), set(ROLES))
        for name, fields in shipped.items():
            role = policy["roles"][name]
            self.assertEqual((fields["name"], fields["model"], fields["effort"]), (name, role["model"], role["effort"]),
                             f"agents/{name}.md khác [roles.{name}] của policy")
            self.assertTrue(fields.get("description"))

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["agents", "--cwd", str(self.project), "--json"])
        calls = {agent["call"] for agent in json.loads(out.getvalue())["agents"]}
        self.assertIn("tester", calls)
        self.assertIn("token-tools:reviewer", calls)  # the shipped ones, from the real plugin folder


if __name__ == "__main__":
    unittest.main()
