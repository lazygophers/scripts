"""tests for lib/claude_session.py"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from lib.claude_session import filter_sessions, list_sessions

NOW = int(time.time() * 1000)


def _write(directory: Path, pid: int, **fields) -> None:
    data = {"pid": pid, "name": f"s-{pid}", "sessionId": f"sid-{pid}",
            "status": "idle", "cwd": "/a/proj", "startedAt": NOW,
            "updatedAt": NOW, **fields}
    (directory / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


class ClaudeSessionTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for f in self.dir.glob("*.json"):
            f.unlink()

    def test_sort_current_dir_first_then_project_then_start_desc(self):
        _write(self.dir, 1, cwd="/x/alpha", startedAt=NOW - 5000)
        _write(self.dir, 2, cwd="/x/alpha", startedAt=NOW - 1000)
        _write(self.dir, 3, cwd="/y/beta", startedAt=NOW)
        rows = list_sessions(self.dir)
        # 无 cwd 命中当前目录：alpha 组在前（项目名升序），组内 start 新→旧
        self.assertEqual([r["pid"] for r in rows], [2, 1, 3])
        self.assertEqual(rows[0]["project"], "alpha")
        self.assertEqual(rows[0]["status"], "空闲")

    def test_current_dir_session_pinned_top(self):
        import os
        old = os.getcwd()
        os.chdir(self.dir)  # 测试目录本身当「当前目录」
        try:
            _write(self.dir, 9, cwd=str(self.dir.resolve()), startedAt=NOW - 9999)
            _write(self.dir, 1, cwd="/x/alpha", startedAt=NOW)
            rows = list_sessions(self.dir)
            self.assertEqual(rows[0]["pid"], 9)
        finally:
            os.chdir(old)

    def test_duplicate_basename_diff_cwd_get_parent(self):
        _write(self.dir, 1, cwd="/home/me/proj")
        _write(self.dir, 2, cwd="/work/proj")
        _write(self.dir, 3, cwd="/x/other")
        rows = {r["pid"]: r["project"] for r in list_sessions(self.dir)}
        self.assertEqual(rows[1], "me/proj")
        self.assertEqual(rows[2], "work/proj")
        self.assertEqual(rows[3], "other")

    def test_same_cwd_same_project(self):
        _write(self.dir, 1, cwd="/a/proj")
        _write(self.dir, 2, cwd="/a/proj")
        rows = {r["pid"]: r["project"] for r in list_sessions(self.dir)}
        self.assertEqual(rows[1], "proj")
        self.assertEqual(rows[2], "proj")

    def test_filters(self):
        _write(self.dir, 1, sessionId="AAAA-1", project_kw=None,
               cwd="/a/one", name="s-1")
        _write(self.dir, 2, sessionId="bbbb-2", cwd="/a/two")
        sessions = list_sessions(self.dir)
        self.assertEqual([r["pid"] for r in filter_sessions(
            sessions, session_kw="aaa")], [1])
        self.assertEqual([r["pid"] for r in filter_sessions(
            sessions, project="one")], [1])
        self.assertEqual([r["pid"] for r in filter_sessions(
            sessions, project="", here="/a/one")], [1])
        self.assertEqual([r["pid"] for r in filter_sessions(
            sessions, project="", here="/nope")], [])

    def test_bad_json_skipped(self):
        (self.dir / "99.json").write_text("not json", encoding="utf-8")
        _write(self.dir, 1)
        self.assertEqual(len(list_sessions(self.dir)), 1)


class EmitFormatTest(unittest.TestCase):
    """cli._emit 三种显式格式 + 默认分流"""

    ROW = ["n1", "sid1", "proj", "忙碌", "09-29 10:00", "09-29 11:00"]

    def _run(self, fmt):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        from lib.cli.claude_session import COLUMNS, _emit

        row = dict(zip((k for _, k in COLUMNS), self.ROW))
        buf = io.StringIO()
        with patch("lib.ai_env.is_ai_shell_env", return_value=False),              redirect_stdout(buf):
            _emit([row], fmt)
        return buf.getvalue()

    def test_json_tsv_csv(self):
        self.assertIn('"name":"n1"', self._run("json").replace(" ", ""))
        self.assertIn("\nn1\tsid1\t", self._run("tsv"))
        out = self._run("csv")
        self.assertTrue(out.startswith("名称,"))
        self.assertIn("sid1", out)


if __name__ == "__main__":
    unittest.main()
