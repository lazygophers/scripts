"""tests for lib/claude_session.py"""
import csv
import io
import json
import shutil
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lib.claude_session import filter_sessions, list_sessions, lookup_title
from lib.cli.claude_session import COLUMNS, _emit, _emit_logs, _parse_args

try:
    from lib.claude_session import filter_session_logs, list_session_logs
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

    ROW = ["n1", "-", "sid1", "proj", "忙碌", "09-29 10:00", "09-29 11:00"]

    def _run(self, fmt):
        row = dict(zip((k for _, k in COLUMNS), self.ROW))
        buf = io.StringIO()
        with patch("lib.ai_env.is_ai_shell_env", return_value=False), \
             redirect_stdout(buf):
            _emit([row], fmt)
        return buf.getvalue()

    def test_json_tsv_csv(self):
        self.assertIn('"name":"n1"', self._run("json").replace(" ", ""))
        self.assertIn("\nn1\t-\tsid1\t", self._run("tsv"))
        out = self._run("csv")
        self.assertTrue(out.startswith("名称,"))
        self.assertIn("sid1", out)


class TestCliEmitAndMain(unittest.TestCase):
    """CLI 层：表格输出、_parse_args、main 的过滤与格式分派。"""

    ROW = ["n1", "-", "sid1", "demo", "busy", "today", "yday"]

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

    def test_parse_args_all_flag(self):
        args = _parse_args(["--all"])
        self.assertTrue(args.all)
        args2 = _parse_args([])
        self.assertFalse(args2.all)

    def test_format_choices_include_table(self):
        args = _parse_args(["--all", "-f", "table"])
        self.assertEqual(args.format, "table")


class MainAllFlagTest(unittest.TestCase):
    """main() 在 --all 下的分派与 -p 过滤。"""

    ROW = ["-", "t1", "sid1", "proj", "busy", "today", "yday"]

    def _row(self, **kw):
        d = dict(zip((k for _, k in COLUMNS), self.ROW))
        d.update(kw)
        return d

    def test_main_all_calls_list_session_logs(self):
        import lib.cli.claude_session as cli
        rows = [self._row()]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs", return_value=rows), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all"])
        self.assertEqual(rc, 0)
        self.assertIn("sid1", buf.getvalue())
        self.assertIn("\t", buf.getvalue())

    def test_main_without_all_keeps_old_path(self):
        import lib.cli.claude_session as cli
        rows = [self._row()]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=rows) as mock_sess, \
             patch("lib.claude_session.list_session_logs") as mock_logs, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session"])
        self.assertEqual(rc, 0)
        mock_sess.assert_called_once()
        mock_logs.assert_not_called()

    def test_main_all_p_filters_by_cwd_hash(self):
        import lib.cli.claude_session as cli

        cwd = "/Users/me/work/myproject"
        cwd_hash = cwd.replace("/", "-")
        # Create a temp projects_root with the expected hash dir
        tmp_root = Path(tempfile.mkdtemp())
        try:
            (tmp_root / cwd_hash).mkdir()
            rows = [
                self._row(cwd=cwd),
                self._row(cwd="/Users/me/other/place"),
            ]
            buf = io.StringIO()
            with patch("lib.claude_session.list_sessions", return_value=[]), \
                 patch("lib.claude_session.list_session_logs",
                       return_value=rows), \
                 patch("lib.cli.claude_session.timed",
                       side_effect=lambda fn, label=None: fn), \
                 patch.object(cli, "_PROJECTS_ROOT", tmp_root), \
                 patch("os.getcwd", return_value=cwd), \
                 patch("lib.ai_env.is_ai_shell_env", return_value=True), \
                 redirect_stdout(buf):
                rc = cli.main(["claude_session", "--all", "-p"])
            self.assertEqual(rc, 0)
            output = buf.getvalue()
            self.assertIn("sid1", output)
            self.assertIn(cwd.split("/")[-1], output)
        finally:
            import shutil
            shutil.rmtree(tmp_root)

    def test_main_all_p_missing_hash_dir_returns_empty(self):
        import lib.cli.claude_session as cli
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=[]), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch.object(cli, "_PROJECTS_ROOT", Path("/no/such/dir")), \
             patch("os.getcwd", return_value="/no/such/dir"), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all", "-p"])
        self.assertEqual(rc, 0)

    def test_main_all_p_with_value_filters_by_project_name(self):
        """--all -p demo filters rows by project field, not hash dir."""
        import lib.cli.claude_session as cli
        rows = [
            self._row(summary="alpha", cwd="/Users/me/work/demo"),
            self._row(summary="beta", cwd="/Users/me/work/other"),
        ]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=rows), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all", "-p", "demo"])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("sid1", output)
        # only demo project row passes
        self.assertEqual(output.count("sid1"), 1)

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


class MainGrepTailTest(unittest.TestCase):
    """main() 在 --all 下 --grep / --tail 的过滤行为。"""

    ROW = ["-", "t1", "sid1", "proj", "busy", "today", "yday"]

    def _row(self, **kw):
        d = dict(zip((k for _, k in COLUMNS), self.ROW))
        d.update(kw)
        return d

    def test_grep_forwarded_to_list_session_logs(self):
        import lib.cli.claude_session as cli
        rows = [self._row(summary="alpha task")]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=rows) as mock_logs, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all", "--grep", "alpha"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().count("sid1"), 1)
        # 全文过滤在 list_session_logs 里做，main 只转发 grep
        self.assertEqual(mock_logs.call_args.kwargs.get("grep"), ["alpha"])

    def test_grep_empty_string_means_no_filter(self):
        import lib.cli.claude_session as cli
        rows = [self._row(summary="alpha"), self._row(summary="beta")]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs", return_value=rows), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all", "--grep", ""])
        self.assertEqual(rc, 0)
        # both rows pass through
        self.assertEqual(buf.getvalue().count("sid1"), 2)

    def test_grep_without_all_is_rejected(self):
        import lib.cli.claude_session as cli
        self.assertRaises(SystemExit, lambda: cli.main(
            ["claude_session", "--grep", "x"]))

    def test_tail_without_all_is_rejected(self):
        import lib.cli.claude_session as cli
        self.assertRaises(SystemExit, lambda: cli.main(
            ["claude_session", "--tail", "5"]))

    def test_tail_passed_to_list_session_logs(self):
        import lib.cli.claude_session as cli
        rows = [self._row()]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=rows) as mock_logs, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all", "--tail", "7"])
        self.assertEqual(rc, 0)
        mock_logs.assert_called_once()
        kwargs = mock_logs.call_args
        # tail should be passed as keyword argument
        self.assertEqual(kwargs.kwargs.get("tail"), 7)

    def test_grep_and_tail_combo(self):
        import lib.cli.claude_session as cli
        rows = [self._row(summary="alpha")]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=rows) as mock_logs, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(
                ["claude_session", "--all", "--tail", "5", "--grep", "alpha"])
        self.assertEqual(rc, 0)
        # both forwarded to list_session_logs
        kwargs = mock_logs.call_args
        self.assertEqual(kwargs.kwargs.get("tail"), 5)
        self.assertEqual(kwargs.kwargs.get("grep"), ["alpha"])
        self.assertEqual(buf.getvalue().count("sid1"), 1)

    def test_grep_repeated_flags_forwarded_as_list(self):
        """重复 --grep 经 argparse append 收成列表转发。"""
        import lib.cli.claude_session as cli
        rows = [self._row(summary="alpha")]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=rows) as mock_logs, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all",
                           "--grep", "alpha", "--grep", "beta"])
        self.assertEqual(rc, 0)
        self.assertEqual(mock_logs.call_args.kwargs.get("grep"),
                         ["alpha", "beta"])


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
            import shutil
            shutil.rmtree(root)

    def test_grep_ai_env_no_progress_output(self):
        """grep 扫描挂进度条，但 AI 环境不得往 stderr 写任何过程输出。"""
        import os
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u1", [
                {"type": "user", "sessionId": "S1", "cwd": "/repo",
                 "gitBranch": "feat/x",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hello needle-xyz"}]}},
            ])
            err = io.StringIO()
            with patch.dict(os.environ, {"CLAUDECODE": "1"}), \
                 redirect_stderr(err):
                rows = list_session_logs(root, grep="needle-xyz")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sessionId"], "S1")
            self.assertEqual(err.getvalue(), "")
        finally:
            shutil.rmtree(root)

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
            self.assertEqual(row["title"], "My Title")
            self.assertEqual(row["summary"], "My Title")
            self.assertEqual(row["first_message"], "hello world")
            self.assertEqual(row["last_messages"], ["hello world"])
        finally:
            import shutil
            shutil.rmtree(root)

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
            self.assertEqual(row["title"], "AI Title")
            self.assertEqual(row["summary"], "AI Title")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_summary_empty_when_no_titles(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u3", [
                {"type": "user", "sessionId": "S3", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["title"], "")
            self.assertEqual(row["summary"], "")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_last_custom_title_wins(self):
        """多次 /rename：最后一条 custom-title 为准（真实文件里按行序覆盖）。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u3b", [
                {"type": "user", "sessionId": "S3b", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
                {"type": "custom-title", "customTitle": "Old Name",
                 "timestamp": "t1"},
                {"type": "custom-title", "customTitle": "New Name",
                 "timestamp": "t2"},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["title"], "New Name")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_custom_title_beats_later_ai_title(self):
        """ai-title 会话中途再生（实测单文件 212 条）；
        custom-title 出现后仍以 custom-title 为权威。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u3c", [
                {"type": "ai-title", "aiTitle": "AI v1", "timestamp": "t0"},
                {"type": "user", "sessionId": "S3c", "timestamp": "t1",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
                {"type": "custom-title", "customTitle": "Renamed",
                 "timestamp": "t2"},
                {"type": "ai-title", "aiTitle": "AI v2", "timestamp": "t3"},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["title"], "Renamed")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_last_ai_title_wins(self):
        """无改名时取最后一条 ai-title（标题再生，最新为准）。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u3d", [
                {"type": "ai-title", "aiTitle": "AI v1", "timestamp": "t0"},
                {"type": "ai-title", "aiTitle": "AI v2", "timestamp": "t1"},
                {"type": "user", "sessionId": "S3d", "timestamp": "t2",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["title"], "AI v2")
        finally:
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

    def test_first_skips_non_human_user_records(self):
        """tool_result / isMeta / 命令包裹的 user 记录不算真实用户消息。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "u10", [
                {"type": "user", "sessionId": "S10", "timestamp": "t0",
                 "isMeta": True,
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "injected caveat"}]}},
                {"type": "user", "timestamp": "t1",
                 "message": {"role": "user", "content":
                             "<bash-input>ls</bash-input>"}},
                {"type": "user", "timestamp": "t2",
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "content": "ok"}]}},
                {"type": "user", "timestamp": "t3", "isSidechain": True,
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "subagent"}]}},
                {"type": "user", "timestamp": "t4",
                 "message": {"role": "user", "content": "real first"}},
                {"type": "user", "timestamp": "t5",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "real last"}]}},
            ])
            [row] = list_session_logs(root)
            self.assertEqual(row["first_message"], "real first")
            self.assertEqual(row["last_messages"], ["real first", "real last"])
        finally:
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

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
            import shutil
            shutil.rmtree(root)

    def test_sort_last_desc_start_desc_session_id_asc(self):
        root = Path(tempfile.mkdtemp())
        try:
            # last desc: t3 > t2 > t1
            self._seed(root, "s1", [
                {"type": "user", "sessionId": "sid-newest-last",
                 "timestamp": "t3",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x"}]}},
                {"type": "custom-title", "customTitle": "New Last",
                 "timestamp": "t3"},
            ])
            self._seed(root, "s2", [
                {"type": "user", "sessionId": "sid-oldest-last",
                 "timestamp": "t1",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "y"}]}},
                {"type": "custom-title", "customTitle": "Old Last",
                 "timestamp": "t1"},
            ])
            # same last (t2), start desc: t2b > t2a
            self._seed(root, "s3", [
                {"type": "user", "sessionId": "sid-later-start",
                 "timestamp": "t2b",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "z"}]}},
                {"type": "custom-title", "customTitle": "Later Start",
                 "timestamp": "t2b"},
            ])
            self._seed(root, "s4", [
                {"type": "user", "sessionId": "sid-earlier-start",
                 "timestamp": "t2a",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "w"}]}},
                {"type": "custom-title", "customTitle": "Earlier Start",
                 "timestamp": "t2a"},
            ])
            rows = list_session_logs(root)
            ids = [r["sessionId"] for r in rows]
            self.assertEqual(ids, [
                "sid-newest-last",
                "sid-later-start",
                "sid-earlier-start",
                "sid-oldest-last",
            ])
        finally:
            import shutil
            shutil.rmtree(root)

    def test_grep_matches_mid_session_tool_call(self):
        """grep 搜整条会话内容：命中藏在中段工具调用里的串。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "g1", [
                {"type": "user", "sessionId": "G1", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "review the pr"}]}},
                {"type": "assistant", "timestamp": "t1",
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "name": "Bash",
                      "input": {"command": "git log "
                                "feature/nico/ipwl-review-timeout"}}]}},
            ])
            self._seed(root, "g2", [
                {"type": "user", "sessionId": "G2", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "unrelated"}]}},
            ])
            rows = list_session_logs(root, grep="ipwl-review-timeout")
            self.assertEqual([r["sessionId"] for r in rows], ["G1"])
        finally:
            import shutil
            shutil.rmtree(root)

    def test_grep_matches_git_branch_and_cwd_case_insensitive(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "g3", [
                {"type": "user", "sessionId": "G3", "timestamp": "t0",
                 "cwd": "/repo", "gitBranch": "feature/nico/ipwl-review-timeout",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hi"}]}},
            ])
            rows = list_session_logs(root, grep="IPWL-REVIEW")
            self.assertEqual([r["sessionId"] for r in rows], ["G3"])
            rows = list_session_logs(root, grep="/repo")
            self.assertEqual([r["sessionId"] for r in rows], ["G3"])
        finally:
            import shutil
            shutil.rmtree(root)

    def test_grep_multiple_keywords_and(self):
        """多条 --grep 之间 AND：全部命中才保留，只命中一条的排除。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "g5", [
                {"type": "user", "sessionId": "G5", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "fork the repo"}]}},
                {"type": "user", "timestamp": "t1",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "邮箱地址是 me@example.com"}]}},
            ])
            self._seed(root, "g6", [
                {"type": "user", "sessionId": "G6", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "fork only, no email"}]}},
            ])
            rows = list_session_logs(root, grep=["fork", "邮箱"])
            self.assertEqual([r["sessionId"] for r in rows], ["G5"])
            # 单条（str 或 list）行为一致
            for kw in ("fork", ["fork"]):
                rows = list_session_logs(root, grep=kw)
                self.assertEqual([r["sessionId"] for r in rows], ["G5", "G6"])
            # 空串关键词被丢弃，等同不过滤
            rows = list_session_logs(root, grep=["", "fork"])
            self.assertEqual([r["sessionId"] for r in rows], ["G5", "G6"])
        finally:
            import shutil
            shutil.rmtree(root)

    def test_grep_skips_oversized_strings(self):
        """超过 _MAX_HAYSTRING_LEN 的字符串（base64/倾倒）不进搜索文本。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "g4", [
                {"type": "user", "sessionId": "G4", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "x" * 200_000
                      + "needle-in-huge"}]}},
            ])
            self.assertEqual(list_session_logs(root, grep="needle-in-huge"), [])
            self.assertEqual(len(list_session_logs(root)), 1)
        finally:
            import shutil
            shutil.rmtree(root)

    def test_grep_matches_title_events(self):
        """--grep 命中标题：custom-title / ai-title 的标题串在搜索文本里。"""
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "g7", [
                {"type": "user", "sessionId": "G7", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hello"}]}},
                {"type": "custom-title", "customTitle": "重构登录模块",
                 "timestamp": "t1"},
            ])
            self._seed(root, "g8", [
                {"type": "user", "sessionId": "G8", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hello"}]}},
                {"type": "ai-title", "aiTitle": "fix vpn routing",
                 "timestamp": "t1"},
            ])
            self._seed(root, "g9", [
                {"type": "user", "sessionId": "G9", "timestamp": "t0",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "hello"}]}},
            ])
            rows = list_session_logs(root, grep="登录模块")
            self.assertEqual([r["sessionId"] for r in rows], ["G7"])
            self.assertEqual(rows[0]["title"], "重构登录模块")
            rows = list_session_logs(root, grep="VPN ROUTING")
            self.assertEqual([r["sessionId"] for r in rows], ["G8"])
            # 无标题的会话 title 为空串
            self.assertEqual(list_session_logs(root)[2]["title"], "")
        finally:
            import shutil
            shutil.rmtree(root)


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


class LookupTitleTest(unittest.TestCase):
    """lookup_title 按 sessionId 关联 projects/*/<sid>.jsonl 取标题。"""

    def _seed(self, root: Path, sid: str, records):
        d = root / "hash-dir"
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{sid}.jsonl").open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_custom_title_beats_ai_title(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "SID-1", [
                {"type": "ai-title", "aiTitle": "AI 名", "timestamp": "t0"},
                {"type": "custom-title", "customTitle": "手动名",
                 "timestamp": "t2"},
            ])
            self.assertEqual(lookup_title("SID-1", root), "手动名")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_last_of_same_type_wins(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "SID-2", [
                {"type": "ai-title", "aiTitle": "旧 AI 名", "timestamp": "t0"},
                {"type": "ai-title", "aiTitle": "新 AI 名", "timestamp": "t1"},
            ])
            self.assertEqual(lookup_title("SID-2", root), "新 AI 名")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_escaped_value_unescaped(self):
        root = Path(tempfile.mkdtemp())
        try:
            self._seed(root, "SID-3", [
                {"type": "custom-title", "customTitle": '说"hi"',
                 "timestamp": "t0"},
            ])
            self.assertEqual(lookup_title("SID-3", root), '说"hi"')
        finally:
            import shutil
            shutil.rmtree(root)

    def test_missing_file_or_empty_sid_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(lookup_title("NOPE", root), "")
            self.assertEqual(lookup_title("", root), "")
        finally:
            import shutil
            shutil.rmtree(root)


class MainTitleEnrichTest(unittest.TestCase):
    """非 --all 路径按 sessionId 关联 jsonl 补标题列。"""

    def test_main_enriches_title_without_all(self):
        import lib.cli.claude_session as cli
        rows = [dict(zip((k for _, k in COLUMNS),
                         ["n1", "-", "sid1", "demo", "busy",
                          "today", "yday"]))]
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions",
                   return_value=rows), \
             patch("lib.claude_session.lookup_title",
                   return_value="终端标题") as mock_t, \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session"])
        self.assertEqual(rc, 0)
        mock_t.assert_called_once_with("sid1")
        self.assertIn("终端标题", buf.getvalue())


class EmitLogsTest(unittest.TestCase):
    """_emit_logs 四种格式 + 默认分流。"""

    ROW = {
        "sessionId": "sid-1",
        "project": "myproj",
        "title": "my title",
        "start": "2026-01-01T00:00:00Z",
        "last": "2026-01-01T01:00:00Z",
        "cwd": "/home/me/myproj",
        "git_branch": "main",
        "kind": "user",
        "summary": "my summary",
        "first_message": "first msg",
        "last_messages": ["last1", "last2", "last3"],
    }

    def _run(self, fmt):
        buf = io.StringIO()
        with patch("lib.ai_env.is_ai_shell_env", return_value=False), \
             redirect_stdout(buf):
            _emit_logs([self.ROW], fmt)
        return buf.getvalue()

    def test_json_is_parseable(self):
        out = self._run("json")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["sessionId"], "sid-1")
        self.assertEqual(data[0]["project"], "myproj")
        # 列统一后 JSON 同构：raw summary 收进 description 列
        self.assertIn("summary=my summary", data[0]["description"])

    def test_tsv_headers_and_description(self):
        out = self._run("tsv")
        lines = out.strip().split("\n")
        self.assertEqual(lines[0],
                         "名称\t标题\t会话ID\t项目\t状态\t开始时间\t最后活跃"
                         "\tcwd\tgit_branch\tkind\tdescription")
        row = lines[1]
        self.assertIn("sid-1", row)
        self.assertIn("myproj", row)
        self.assertIn("my title", row)
        # status 在 --all 模式取不到，占位 '-'
        self.assertIn("\t-\t", "\t" + row + "\t")
        # description: 3 parts joined by \n within the tab cell
        parts = row.split("\t")
        desc = parts[-1]
        self.assertIn("summary=my summary", desc)
        self.assertIn("first=first msg", desc)
        self.assertIn("last=last1 | last2 | last3", desc)

    def test_csv_headers_and_description(self):
        out = self._run("csv")
        reader = csv.reader(io.StringIO(out))
        rows = list(reader)
        self.assertEqual(rows[0],
                         ["名称", "标题", "会话ID", "项目", "状态",
                          "开始时间", "最后活跃", "cwd", "git_branch",
                          "kind", "description"])
        row = rows[1]
        # 名称(--all 取不到) / 标题 / 会话ID / 项目 / 状态(-) / … / description
        self.assertEqual(row[0], "-")
        self.assertEqual(row[1], "my title")
        self.assertEqual(row[2], "sid-1")
        self.assertEqual(row[3], "myproj")
        self.assertEqual(row[4], "-")
        self.assertIn("summary=my summary", row[10])
        self.assertIn("first=first msg", row[10])
        self.assertIn("last=last1 | last2 | last3", row[10])

    def test_table_contains_session_id(self):
        out = self._run("table")
        # 11 列后 rich 表格截断长单元格（sid-1 → sid…），断言未截断的列
        self.assertIn("main", out)
        self.assertIn("user", out)

    def test_default_fmt_ai_shell_uses_tsv(self):
        """main --all with is_ai_shell_env=True → tsv output."""
        import lib.cli.claude_session as cli
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=[self.ROW]), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=True), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all"])
        self.assertEqual(rc, 0)
        self.assertIn("\t", buf.getvalue())

    def test_default_fmt_human_shell_uses_table(self):
        """main --all with is_ai_shell_env=False → Rich table output."""
        import lib.cli.claude_session as cli
        buf = io.StringIO()
        with patch("lib.claude_session.list_sessions", return_value=[]), \
             patch("lib.claude_session.list_session_logs",
                   return_value=[self.ROW]), \
             patch("lib.cli.claude_session.timed",
                   side_effect=lambda fn, label=None: fn), \
             patch("lib.ai_env.is_ai_shell_env", return_value=False), \
             redirect_stdout(buf):
            rc = cli.main(["claude_session", "--all"])
        self.assertEqual(rc, 0)
        # rich 表格截断长单元格，断言未截断的 git_branch 列
        self.assertIn("main", buf.getvalue())

    def test_description_empty_fields(self):
        """空字段不崩溃，description 仍有三段。"""
        row = {"sessionId": "x", "project": "", "start": "", "last": "",
               "cwd": "", "git_branch": "", "kind": "",
               "summary": "", "first_message": "", "last_messages": []}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit_logs([row], "tsv")
        out = buf.getvalue()
        self.assertIn("summary=", out)
        self.assertIn("first=", out)
        self.assertIn("last=", out)


class ColumnUnifyTest(unittest.TestCase):
    """两种模式列结构一致：同列数、同名表头同位置、占位 '-'。"""

    def _headers(self, fn, rows):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(rows, "tsv")
        return buf.getvalue().split("\n")[0].split("\t")

    def test_both_modes_share_column_structure(self):
        # 非 --all 行：sessions/*.json 的字段，有 name 无 title
        non_all = {"name": "n1", "sessionId": "sid1", "project": "proj",
                   "status": "busy", "start": "today", "active": "yday"}
        # --all 行：jsonl 路径字段，有 title 无 name / status，最后活跃叫 last
        all_row = {"sessionId": "sid2", "project": "proj", "title": "n2",
                   "start": "2026-01-01T00:00:00Z", "last": "2026-01-01T01:00:00Z",
                   "cwd": "/r", "git_branch": "main", "kind": "user",
                   "summary": "s", "first_message": "f", "last_messages": ["l"]}
        h1 = self._headers(_emit, [non_all])
        h2 = self._headers(_emit_logs, [all_row])
        self.assertEqual(h1, h2)
        row1 = self._cells(_emit, [non_all])
        row2 = self._cells(_emit_logs, [all_row])
        self.assertEqual(len(row1), len(row2))
        # 同名列同位置：标题 / sessionId / project
        self.assertEqual(row1[0], "n1")
        self.assertEqual(row1[1], "-")   # 非 --all 无标题
        self.assertEqual(row2[0], "-")   # --all 无互通名称
        self.assertEqual(row2[1], "n2")
        self.assertEqual(row1[2], "sid1")
        self.assertEqual(row2[2], "sid2")
        self.assertEqual(row1[3], row2[3])
        # 各自取不到的列占位 '-'
        self.assertEqual(row1[-4:], ["-", "-", "-", "-"])
        self.assertEqual(row2[4], "-")  # status
        # --all 的 last 映射进统一的「最后活跃」列
        self.assertEqual(row2[6], "2026-01-01T01:00:00Z")

    def _cells(self, fn, rows):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(rows, "tsv")
        return buf.getvalue().split("\n")[1].split("\t")


if __name__ == "__main__":
    unittest.main()
