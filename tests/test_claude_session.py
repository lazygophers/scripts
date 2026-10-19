"""tests for lib/claude_session.py"""
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from lib.claude_session import filter_sessions, list_sessions
from lib.cli.claude_session import COLUMNS, _emit, _parse_args

try:
    from lib.claude_session import list_session_logs, filter_session_logs
except ImportError:
    list_session_logs = None
    filter_session_logs = None

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
        self.assertEqual([r["pid"] for r in rows], [2, 1, 3])
        self.assertEqual(rows[0]["project"], "alpha")
        self.assertEqual(rows[0]["status"], "空闲")

    def test_current_dir_session_pinned_top(self):
        import os
        old = os.getcwd()
        os.chdir(self.dir)
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
        _write(self.dir, 1, sessionId="AAAA-1", cwd="/a/one", name="s-1")
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
        row = dict(zip((k for _, k in COLUMNS), self.ROW))
        buf = io.StringIO()
        with patch("lib.ai_env.is_ai_shell_env", return_value=False), \
             redirect_stdout(buf):
            _emit([row], fmt)
        return buf.getvalue()

    def test_json_tsv_csv(self):
        self.assertIn('"name":"n1"', self._run("json").replace(" ", ""))
        self.assertIn("\nn1\tsid1\t", self._run("tsv"))
        out = self._run("csv")
        self.assertTrue(out.startswith("名称,"))
        self.assertIn("sid1", out)


class TestCliEmitAndMain(unittest.TestCase):
    """CLI 层：表格输出、_parse_args、main 的过滤与格式分派。"""

    ROW = ["n1", "sid1", "demo", "busy", "today", "yday"]

    def test_table_format_renders_rich_table(self):
        row = dict(zip((k for _, k in COLUMNS), self.ROW))
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit(row and [row], "table")
        self.assertIn("Claude Code 会话", buf.getvalue())
        self.assertIn("sid1", buf.getvalue())

    def test_parse_args_flags(self):
        args = _parse_args(["-s", "abc", "-f", "json"])
        self.assertEqual((args.session, args.format), ("abc", "json"))
        args2 = _parse_args(["--project"])
        self.assertEqual(args2.project, "")
        args3 = _parse_args(["--project", "demo"])
        self.assertEqual(args3.project, "demo")

    def test_main_filters_and_emits(self):
        import lib.cli.claude_session as cli

        sessions = [dict(zip((k for _, k in COLUMNS), self.ROW))]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=sessions), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "-s", "sid1"])
        self.assertEqual(rc, 0)
        self.assertIn("sid1", buf.getvalue())
        self.assertIn("\t", buf.getvalue())

        with patch("lib.claude_session.list_sessions", return_value=sessions), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=False), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "-f", "csv"])
        self.assertEqual(rc, 0)
        self.assertIn("sid1", buf.getvalue())


class ListSessionLogsTest(unittest.TestCase):
    """list_session_logs(projects_root, tail) 扫描
    projects_root/<hash>/<uuid>.jsonl，返回核心字段。"""

    def _seed(self, root: Path, uuid: str, records, hash_dir: str = "proj-hash"):
        d = root / hash_dir
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{uuid}.jsonl").open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_empty_dir(self):
        root = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(list_session_logs(root), [])
        finally:
            import shutil; shutil.rmtree(root)

    def test_basic_session_fields(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u1", [
                {"type": "user", "sessionId": "S1",
                 "timestamp": "2026-01-01T00:00:00Z",
                 "cwd": "/repo", "gitBranch": "feat/x", "kind": "user",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hello world"}]}},
                {"type": "assistant",
                 "timestamp": "2026-01-01T00:00:05Z",
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "hi"}]}},
                {"type": "custom-title", "customTitle": "My Title",
                 "timestamp": "2026-01-01T00:01:00Z"},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["sessionId"], "S1")
            self.assertEqual(row["start"], "2026-01-01T00:00:00Z")
            self.assertEqual(row["last"], "2026-01-01T00:01:00Z")
            self.assertEqual(row["cwd"], "/repo")
            self.assertEqual(row["git_branch"], "feat/x")
            self.assertEqual(row["kind"], "user")
            self.assertEqual(row["summary"], "My Title")
            self.assertEqual(row["first_message"], "hello world")
            self.assertEqual(row["last_messages"], ["hello world"])
        finally:
            import shutil; shutil.rmtree(root)

    def test_summary_falls_back_to_ai_title(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u2", [
                {"type": "user", "sessionId": "S2", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "go"}]}},
                {"type": "ai-title", "aiTitle": "AI Title",
                 "timestamp": "t1"},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["summary"], "AI Title")
        finally:
            import shutil; shutil.rmtree(root)

    def test_summary_empty_when_no_titles(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u3", [
                {"type": "user", "sessionId": "S3", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["summary"], "")
        finally:
            import shutil; shutil.rmtree(root)

    def test_user_block_filters_tools_and_images(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u4", [
                {"type": "user", "sessionId": "S4", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "first "},
                     {"type": "tool_use", "name": "Bash", "input": {}},
                     {"type": "tool_result",
                      "content": [{"type": "text", "text": "x"}]},
                     {"type": "image", "source": {}},
                     {"type": "thinking", "thinking": "hmm"},
                     {"type": "text", "text": "part"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["first_message"], "first part")
        finally:
            import shutil; shutil.rmtree(root)

    def test_last_messages_takes_tail(self):
        root = Path(tempfile.mkdtemp())
        try:
            records = [
                {"type": "user", "sessionId": "S5", "timestamp": f"t{i}",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": f"msg{i}"}]}}
                for i in range(5)
            ]
            self._seed(root, "u5", records)
            [row] = list_session_logs(root, tail=3)
            self.assertEqual(row["last_messages"], ["msg2", "msg3", "msg4"])
        finally:
            import shutil; shutil.rmtree(root)

    def test_last_messages_default_three(self):
        root = Path(tempfile.mkdtemp())
        try:
            records = [
                {"type": "user", "sessionId": "S6", "timestamp": f"t{i}",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": f"m{i}"}]}}
                for i in range(10)
            ]
            self._seed(root, "u6", records)
            [row] = list_session_logs(root)
            self.assertEqual(row["last_messages"], ["m7", "m8", "m9"])
        finally:
            import shutil; shutil.rmtree(root)

    def test_no_user_records(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u7", [
                {"type": "assistant", "sessionId": "S7", "timestamp": "t0",
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "yo"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["first_message"], "")
            self.assertEqual(row["last_messages"], [])
        finally:
            import shutil; shutil.rmtree(root)

    def test_bad_jsonl_skipped(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u8", [
                {"type": "user", "sessionId": "S8", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "ok"}]}},
            ])
            d = root / "proj-hash"
            with (d / "bad.jsonl").open("w", encoding="utf-8") as fh:
                fh.write("{this is not json\n")
            rows = list_session_logs(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sessionId"], "S8")
        finally:
            import shutil; shutil.rmtree(root)

    def test_corrupt_file_inside_dir_does_not_kill_others(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u9a", [
                {"type": "user", "sessionId": "A", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "a"}]}},
            ], hash_dir="h1")
            self._seed(root, "u9b", [
                {"type": "user", "sessionId": "B", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "b"}]}},
            ], hash_dir="h2")
            (root / "h1" / "u9a.jsonl").write_text(
                "garbage\n", encoding="utf-8")
            rows = list_session_logs(root)
            ids = sorted(r["sessionId"] for r in rows)
            self.assertEqual(ids, ["B"])
        finally:
            import shutil; shutil.rmtree(root)


class FilterSessionLogsTest(unittest.TestCase):
    """filter_session_logs(rows, grep) 消歧 + 子串过滤。"""

    def _row(self, sid, *, cwd, summary="", first="", last=None):
        return {"sessionId": sid, "start": "t0", "last": "t1",
                "cwd": cwd, "git_branch": "main", "kind": "user",
                "summary": summary, "first_message": first,
                "last_messages": last or []}

    def test_project_basename_collision_upgrades_to_parent(self):
        rows = [self._row("a", cwd="/home/me/proj"),
                self._row("b", cwd="/work/proj"),
                self._row("c", cwd="/x/other")]
        out = filter_session_logs(rows)
        self.assertEqual({r["sessionId"]: r["project"] for r in out},
                         {"a": "me/proj", "b": "work/proj", "c": "other"})

    def test_project_same_cwd_keeps_bare_basename(self):
        rows = [self._row("a", cwd="/a/proj"),
                self._row("b", cwd="/a/proj")]
        out = filter_session_logs(rows)
        self.assertEqual({r["sessionId"]: r["project"] for r in out},
                         {"a": "proj", "b": "proj"})

    def test_project_empty_cwd_becomes_dash(self):
        rows = [self._row("a", cwd="")]
        self.assertEqual(filter_session_logs(rows)[0]["project"], "-")

    def test_grep_matches_summary(self):
        rows = [self._row("a", cwd="/r1", summary="alpha summary"),
                self._row("b", cwd="/r2", summary="other")]
        out = filter_session_logs(rows, grep="alpha")
        self.assertEqual([r["sessionId"] for r in out], ["a"])

    def test_grep_matches_first_message(self):
        rows = [self._row("a", cwd="/r1", first="hello world"),
                self._row("b", cwd="/r2", first="goodbye")]
        out = filter_session_logs(rows, grep="hello")
        self.assertEqual([r["sessionId"] for r in out], ["a"])

    def test_grep_matches_last_messages_element(self):
        rows = [self._row("a", cwd="/r1", last=["one", "two", "three"]),
                self._row("b", cwd="/r2", last=["four"])]
        out = filter_session_logs(rows, grep="two")
        self.assertEqual([r["sessionId"] for r in out], ["a"])

    def test_grep_is_case_insensitive(self):
        rows = [self._row("a", cwd="/r1", summary="Hello World"),
                self._row("b", cwd="/r2", first="ALPHA start")]
        self.assertEqual([r["sessionId"] for r in
                          filter_session_logs(rows, grep="hello")], ["a"])
        self.assertEqual([r["sessionId"] for r in
                          filter_session_logs(rows, grep="alpha")], ["b"])

    def test_grep_no_match_returns_empty(self):
        rows = [self._row("a", cwd="/r", summary="x", first="y",
                           last=["z"])]
        self.assertEqual(filter_session_logs(rows, grep="nope"), [])

    def test_grep_none_returns_all_enriched(self):
        rows = [self._row("a", cwd="/r", summary="s", first="f",
                           last=["l"])]
        out = filter_session_logs(rows)
        self.assertEqual([r["sessionId"] for r in out], ["a"])
        self.assertEqual(out[0]["project"], "r")

    def test_filter_does_not_mutate_input(self):
        rows = [self._row("a", cwd="/a/proj")]
        before = json.dumps(rows, sort_keys=True)
        filter_session_logs(rows)
        self.assertEqual(json.dumps(rows, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
