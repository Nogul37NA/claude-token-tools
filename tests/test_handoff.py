import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ctt import handoff
from ctt.cli import main

NOTE = """# Bàn giao: demo

## Trạng thái
- nhánh main, sạch.

## Quyết định đã chốt
- Không thêm Co-Authored-By.

## Việc cần làm tiếp
1. Viết lệnh handoff.

## File liên quan
`ctt/handoff.py`
"""


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"CTT_HOME": str(self.root / "home")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_slug_drops_vietnamese_marks(self):
        self.assertEqual(handoff.slugify("Token Tools: lệnh handoff"), "token-tools-lenh-handoff")
        self.assertEqual(handoff.slugify("Đo đạc ĐẦU VÀO"), "do-dac-dau-vao")
        self.assertEqual(handoff.slugify("!!!"), "ban-giao")

    def test_save_never_overwrites(self):
        first, tokens = handoff.save("token tools", NOTE, today=date(2026, 11, 12))
        second, _ = handoff.save("token tools", NOTE, today=date(2026, 11, 12))
        self.assertEqual(first.name, "2026-11-12-token-tools.md")
        self.assertEqual(second.name, "2026-11-12-token-tools-2.md")
        self.assertEqual(first.parent, self.root / "home" / "handoffs")
        self.assertEqual(first.read_text(encoding="utf-8"), NOTE)
        self.assertLess(tokens, 200)

    def test_too_long_or_missing_section_is_refused(self):
        with self.assertRaisesRegex(ValueError, "quá giới hạn 2000"):
            handoff.save("x", NOTE + "chữ " * 1500)
        with self.assertRaisesRegex(ValueError, "Quyết định đã chốt"):
            handoff.save("x", NOTE.replace("## Quyết định đã chốt", "## Ghi chú"))
        self.assertFalse((self.root / "home" / "handoffs").exists() and any((self.root / "home" / "handoffs").iterdir()))

    def test_estimate_errs_high(self):
        self.assertEqual(handoff.estimate_tokens("a" * 2500), 1000)

    def test_facts_from_git(self):
        repo = self.root / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        (repo / "a.txt").write_text("1", encoding="utf-8")
        subprocess.run([*git, "add", "a.txt"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "first"], check=True)
        (repo / "a.txt").write_text("2", encoding="utf-8")
        (repo / "b.txt").write_text("new", encoding="utf-8")
        subprocess.run([*git, "add", "b.txt"], check=True)
        (repo / "c.txt").write_text("loose", encoding="utf-8")
        found = handoff.facts(repo)
        self.assertEqual(found["branch"], "main")
        self.assertIn("first", found["last_commits"][0])
        self.assertEqual((found["staged"], found["unstaged"], found["untracked"]), (["b.txt"], ["a.txt"], ["c.txt"]))
        self.assertEqual(found["remotes"], [])
        self.assertIn("## Việc cần làm tiếp", found["template"])

    def test_facts_outside_git(self):
        found = handoff.facts(self.root)
        self.assertIsNone(found["repo"])
        self.assertEqual(found["max_tokens"], 2000)

    def test_cli_save_and_refusal(self):
        draft = self.root / "draft.md"
        draft.write_text(NOTE, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["handoff-save", "--topic", "Demo bàn giao", "--file", str(draft)])
        self.assertIn("demo-ban-giao.md", out.getvalue())
        draft.write_text("ngắn", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            main(["handoff-save", "--topic", "x", "--file", str(draft)])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("thiếu mục", err.getvalue())

    def test_cli_facts_is_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["handoff-facts", "--cwd", str(self.root)])
        self.assertIn("handoffs", json.loads(out.getvalue())["handoffs_dir"])


if __name__ == "__main__":
    unittest.main()
