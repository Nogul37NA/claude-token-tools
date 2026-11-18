import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt import permissions
from ctt.cli import main


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"CTT_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_give_every_role_and_the_agent_map(self):
        profile = permissions.load_permissions()
        self.assertEqual(profile["roles"]["tester"]["write"], ["**/src/test/**", "**/tests/**", "**/test/**", "**/__tests__/**", "**/test_*.py", "**/*_test.*", "**/*.test.*", "**/*.spec.*"])
        self.assertEqual(profile["roles"]["tester"]["outside"], "deny")
        self.assertEqual(profile["roles"]["developer"]["ask_write"], ["**/db/migration/**", "**/migrations/**"])
        self.assertEqual(profile["roles"]["reviewer"]["write"], [])
        self.assertEqual(profile["agent_role"]["code-reviewer"], "reviewer")
        self.assertIn("git diff", profile["common"]["bash"])
        self.assertTrue(any("push" in pattern for pattern in profile["always_ask"]["bash"]))

    def test_user_copy_overrides_only_what_it_names(self):
        Path(self.home.name, "permissions.toml").write_text(
            '[roles.tester]\nwrite = ["**/src/test/**", "**/testdata/**"]\n', encoding="utf-8")
        profile = permissions.load_permissions()
        self.assertEqual(profile["roles"]["tester"]["write"], ["**/src/test/**", "**/testdata/**"])
        self.assertEqual(profile["roles"]["tester"]["outside"], "deny")  # kept from the defaults
        self.assertIn("developer", profile["roles"])

    def test_a_bad_profile_is_refused_with_the_place(self):
        Path(self.home.name, "permissions.toml").write_text('[roles.docs]\noutside = "maybe"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"roles\.docs"):
            permissions.load_permissions()
        Path(self.home.name, "permissions.toml").write_text("[always_ask]\nbash = ['(']\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "always_ask"):
            permissions.load_permissions()

    def test_set_role_writes_the_override_and_keeps_the_rest(self):
        override = Path(self.home.name, "permissions.toml")
        override.write_text("[always_ask]\nbash = ['\\bgit\\s+push\\b']\n", encoding="utf-8")
        defaults = permissions.load_permissions()["roles"]["tester"]["write"]
        profile = permissions.set_role("tester", add=("write", "**/testdata/**"))
        self.assertEqual(profile["roles"]["tester"]["write"], [*defaults, "**/testdata/**"])
        self.assertEqual(profile["always_ask"]["bash"], ["\\bgit\\s+push\\b"])  # other tables survive, as typed
        self.assertTrue(Path(self.home.name, "permissions.toml.bak").exists())
        profile = permissions.set_role("tester", remove=("write", "**/src/test/**"), outside="ask", enabled=False)
        tester = profile["roles"]["tester"]
        self.assertEqual((tester["write"], tester["outside"], tester["enabled"]),
                         ([*defaults[1:], "**/testdata/**"], "ask", False))
        self.assertTrue(profile["roles"]["developer"]["enabled"])
        profile = permissions.set_role("tester", reset=True)
        self.assertEqual(profile["roles"]["tester"]["write"][0], "**/src/test/**")
        self.assertTrue(profile["roles"]["tester"]["enabled"])
        self.assertIn("bản riêng của máy này", override.read_text(encoding="utf-8"))

    def test_budget_caps_and_role_models_come_from_policy(self):
        profile = permissions.load_permissions()
        self.assertEqual(profile["budget"], {"roles": {}, "enforce_model": False, "raise_share": 0.5})
        self.assertEqual(profile["role_models"]["tester"], "sonnet")
        Path(self.home.name, "policy.toml").write_text("[budget.roles]\ntester = 3_000_000\n", encoding="utf-8")
        self.assertEqual(permissions.load_permissions()["budget"]["roles"], {"tester": 3_000_000})
        Path(self.home.name, "policy.toml").write_text("[budget.roles]\ntester = -1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "budget.roles"):
            permissions.load_permissions()

    def test_session_caps_come_from_policy(self):
        self.assertEqual(permissions.load_permissions()["sessions"], {"max_depth": 2, "max_children": 4})
        Path(self.home.name, "policy.toml").write_text("[sessions]\nmax_depth = 1\n", encoding="utf-8")
        self.assertEqual(permissions.load_permissions()["sessions"], {"max_depth": 1, "max_children": 4})
        Path(self.home.name, "policy.toml").write_text("[sessions]\nmax_children = -2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "max_children"):
            permissions.load_permissions()

    def test_mcp_setting_defaults_to_allow_and_is_checked(self):
        profile = permissions.load_permissions()
        self.assertEqual({role["mcp"] for role in profile["roles"].values()}, {"allow"})
        self.assertTrue(any("delete" in pattern for pattern in profile["always_ask"]["mcp"]))
        profile = permissions.set_role("reviewer", mcp="deny", add=("mcp_allow", "mcp__jira__get_*"))
        self.assertEqual((profile["roles"]["reviewer"]["mcp"], profile["roles"]["reviewer"]["mcp_allow"]),
                         ("deny", ["mcp__jira__get_*"]))
        self.assertIn("tool MCP (không chỉ đọc): chặn · luôn cho: mcp__jira__get_*", permissions.describe(profile))
        with self.assertRaisesRegex(ValueError, "mcp"):
            permissions.set_role("reviewer", mcp="maybe")
        Path(self.home.name, "permissions.toml").write_text("[roles.tester]\nmcp = 'sometimes'\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "roles.tester"):
            permissions.load_permissions()
        Path(self.home.name, "permissions.toml").write_text("[always_ask]\nmcp = ['(']\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "always_ask. mcp"):
            permissions.load_permissions()

    def test_auto_mode_ask_defaults_to_ask_and_is_checked(self):
        profile = permissions.load_permissions()
        self.assertEqual(profile["auto_mode"], {"ask": "ask"})
        self.assertIn("phiên ở chế độ Auto: chỗ lẽ ra hỏi bạn thì để app quyết", permissions.describe(profile))
        override = Path(self.home.name, "permissions.toml")
        override.write_text("[auto_mode]" + chr(10) + "ask = 'deny'" + chr(10), encoding="utf-8")
        profile = permissions.load_permissions()
        self.assertEqual(profile["auto_mode"], {"ask": "deny"})
        self.assertIn("thì CHẶN", permissions.describe(profile))
        self.assertEqual(permissions.set_role("tester", outside="ask")["auto_mode"], {"ask": "deny"})  # survives a pane edit
        override.write_text("[auto_mode]" + chr(10) + "ask = 'maybe'" + chr(10), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "auto_mode"):
            permissions.load_permissions()

    def test_set_role_refuses_bad_input(self):
        for kwargs in ({"add": ("write", "  ")}, {"add": ("nope", "x")}, {"outside": "maybe"}):
            with self.assertRaises(ValueError):
                permissions.set_role("tester", **kwargs)
        with self.assertRaisesRegex(ValueError, "không có vai"):
            permissions.set_role("ghost", enabled=False)

    def test_values_with_quotes_round_trip(self):
        permissions.set_role("docs", add=("bash", "echo 'x'"))
        self.assertIn("echo 'x'", permissions.load_permissions()["roles"]["docs"]["bash"])

    def test_cli_set_answers_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["permissions-set", "--role", "tester", "--enabled", "false"])
        self.assertFalse(json.loads(out.getvalue())["roles"]["tester"]["enabled"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["permissions-set", "--role", "ghost", "--enabled", "false"])
        self.assertIn("không có vai", json.loads(out.getvalue())["error"])

    def test_cli_json_for_the_mod_and_table_for_people(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["permissions", "--json"])
        self.assertEqual(json.loads(out.getvalue())["roles"]["docs"]["write"], ["docs/**", "*.md"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["permissions"])
        self.assertIn("reviewer (code-reviewer)", out.getvalue())
        self.assertIn("ngoài quyền: chặn", out.getvalue())


if __name__ == "__main__":
    unittest.main()
