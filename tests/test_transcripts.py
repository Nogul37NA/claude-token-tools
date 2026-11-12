import tempfile
import unittest
from pathlib import Path

from ctt.transcripts import scan
from tests.fixtures import build_projects


class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = scan(build_projects(Path(cls.temp.name)))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_split_message_counted_once(self):
        ids = [(r.session_id, r.output_tokens) for r in self.result.requests if r.session_id == "s1"]
        self.assertEqual(len(ids), 3)  # m1 once, m2, subagent m3
        self.assertEqual(sum(o for _, o in ids), 100 + 50 + 20)

    def test_corrupt_line_counted_not_fatal(self):
        self.assertEqual(self.result.bad_lines, 1)
        self.assertEqual(self.result.files, 3)

    def test_subagent_flag_and_session(self):
        sub = [r for r in self.result.requests if r.is_subagent]
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0].session_id, "s1")
        self.assertEqual(sub[0].model, "claude-sonnet-5-5")

    def test_cache_write_split(self):
        m1 = next(r for r in self.result.requests if r.output_tokens == 100)
        self.assertEqual((m1.cache_write_5m, m1.cache_write_1h), (0, 500))  # no breakdown → 1h
        m2 = next(r for r in self.result.requests if r.output_tokens == 50)
        self.assertEqual((m2.cache_write_5m, m2.cache_write_1h), (100, 200))
        self.assertEqual(m2.context, 2000 + 300)

    def test_session_meta(self):
        meta = self.result.sessions["s1"]
        self.assertEqual(meta.title, "Đơn hàng lỗi")
        self.assertEqual(meta.first_prompt, "Sửa lỗi danh sách đơn hàng")  # skips <command…>, collapses spaces
        self.assertEqual(meta.project, "demo")
        self.assertEqual(meta.branch, "main")

    def test_project_is_launch_folder_not_subfolder_or_encoded_name(self):
        self.assertEqual(self.result.sessions["s2"].project, "other")
        self.assertEqual(self.result.sessions["s2"].title, "Hỏi nhanh")

    def test_launch_folder_fallbacks(self):
        from ctt.transcripts import encode_folder, launch_folder_name
        self.assertEqual(encode_folder("C:\\Users\\dev\\projects\\shop_be"), "C--Users-dev-projects-shop-be")
        self.assertEqual(launch_folder_name("x-nomatch", {"C:\\a\\proj\\sub", "C:\\a\\proj"}), "proj")  # shortest
        self.assertEqual(launch_folder_name("C--raw", set()), "C--raw")

    def test_tool_uses_deduplicated_by_id(self):
        names = [t.name for t in self.result.tool_uses if t.session_id == "s1"]
        self.assertEqual(names.count("Edit"), 2)
        self.assertEqual(names.count("Bash"), 2)


if __name__ == "__main__":
    unittest.main()
