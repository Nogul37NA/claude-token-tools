import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctt.cli import main
from tests.fixtures import build_projects


class CliTest(unittest.TestCase):
    def test_calibrate_and_report_write_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build_projects(root / "projects")
            out = root / "out"
            buffer = io.StringIO()
            with mock.patch.dict(os.environ, {"CTT_HOME": str(root / "home")}), contextlib.redirect_stdout(buffer):
                main(["calibrate", "--days", "10000", "--projects-dir", str(root / "projects"), "--out", str(out)])
                main(["report", "--days", "10000", "--projects-dir", str(root / "projects"), "--out", str(out)])
            text = buffer.getvalue()
            self.assertIn("2 phiên", text)
            self.assertIn("model không có trong bảng giá", text)
            self.assertTrue((out / "calibration_sessions.csv").exists())
            self.assertTrue((out / "usage_by_day.csv").exists())
            self.assertIn("THEO PROJECT (trong kỳ)", text)
            self.assertIn("other", text)
            self.assertNotIn("C--work", text)  # project names resolved, not encoded folders
            import csv
            rows = list(csv.DictReader((out / "usage_by_day.csv").open(encoding="utf-8-sig")))
            # 2026-11-10: m2 (opus) + m3 (sonnet subagent) + m4 (unknown → opus fallback, 1M output = $20)
            day = next(r for r in rows if r["day"] == "2026-11-10")
            expected = (50 * 20 + 2000 * 0.2 + 100 * 5 + 200 * 8 + 20 * 10 + 400 * 0.1 + 1_000_000 * 20) / 1e6
            self.assertAlmostEqual(float(day["cost_usd"]), expected, places=4)


    def test_init_creates_then_replaces_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "a", root / "b"
            first.mkdir()
            second.mkdir()
            home = root / "home"
            with mock.patch.dict(os.environ, {"CTT_HOME": str(home)}), contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--root", str(first)])
                (home / "policy.toml").write_text((home / "policy.toml").read_text(encoding="utf-8")
                                                  + "\n[projects.x.roles]\ndeveloper = { effort = \"high\" }\n",
                                                  encoding="utf-8")
                main(["init", "--root", str(first), "--root", str(second)])
            text = (home / "policy.toml").read_text(encoding="utf-8")
            self.assertEqual(text.count("roots ="), 1)
            self.assertIn(str(second.resolve()), text)
            self.assertIn("[projects.x.roles]", text)  # other user settings kept

    def test_init_rejects_missing_folder(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"CTT_HOME": temp}):
            with self.assertRaises(SystemExit):
                main(["init", "--root", str(Path(temp) / "nope")])

    def test_help_of_every_command_prints(self):
        # a bare % in a help text made `ctt --help` crash (0.17.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as done:
            main(["--help"])
        self.assertEqual(done.exception.code, 0)
        for command in ("limits", "ship-start", "ship-stage", "ship-step", "ship-finish", "estimate"):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as done:
                main([command, "--help"])
            self.assertEqual(done.exception.code, 0, command)

    def test_version_option_prints_version_and_exits_zero(self):
        from ctt import __version__
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as done:
            main(["--version"])
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"ctt {__version__}")


if __name__ == "__main__":
    unittest.main()
