"""lib/commit_wf.py 的 API 路径与失败分支测试。

tests/test_commit_wf.py 覆盖的是 prompt 拼装和 run_commit 主路径；这里补
LAZYGOPHERS /chat/compate 调用（含各类网络/协议错误）、思考散文剥离、
index.lock 自愈、批量 commit_all。不发真实网络请求，urlopen 全是假的。
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import commit_wf as cw  # noqa: E402


ENV = {"LAZYGOPHERS_SCRIPTS_BASE_URL": "https://api.example.com/v1",
       "LAZYGOPHERS_SCRIPTS_TOKEN": "tok"}


def _resp(payload: dict):
    """假的 urlopen 上下文管理器，read() 吐 JSON。"""
    body = json.dumps(payload).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = SimpleNamespace(read=lambda: body)
    cm.__exit__.return_value = False
    return cm


def _p(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestLazygophersEnabled(unittest.TestCase):
    def test_needs_both_env_vars(self) -> None:
        with mock.patch.dict(cw.os.environ, ENV):
            self.assertTrue(cw._lazygophers_enabled())
        with mock.patch.dict(cw.os.environ, {**ENV, "LAZYGOPHERS_SCRIPTS_TOKEN": ""}):
            self.assertFalse(cw._lazygophers_enabled())
        with mock.patch.dict(cw.os.environ, {**ENV, "LAZYGOPHERS_SCRIPTS_BASE_URL": ""}):
            self.assertFalse(cw._lazygophers_enabled())


class TestExtractMessage(unittest.TestCase):
    def test_picks_the_line_where_the_message_starts(self) -> None:
        texts = ["我先分析一下这次改动。\nfeat: 加缓存\n\n- 细节", ]
        self.assertEqual(cw._extract_message(texts), "feat: 加缓存\n\n- 细节")

    def test_scans_across_blocks(self) -> None:
        texts = ["思考散文", "fix(auth): 修 token 过期判断"]
        self.assertEqual(cw._extract_message(texts), "fix(auth): 修 token 过期判断")

    def test_breaking_change_and_scope_are_recognised(self) -> None:
        self.assertEqual(cw._extract_message(["refactor(api)!: 换签名"]),
                         "refactor(api)!: 换签名")

    def test_falls_back_to_the_last_block(self) -> None:
        self.assertEqual(cw._extract_message(["a", "随手写的一行"]), "随手写的一行")

    def test_long_fallback_is_rejected_as_prose(self) -> None:
        self.assertEqual(cw._extract_message(["x" * 400]), "")

    def test_empty_input(self) -> None:
        self.assertEqual(cw._extract_message([]), "")


class TestGenerateViaLazygophers(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(cw.os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def _call(self, urlopen):
        with mock.patch.object(cw.urllib.request, "urlopen", urlopen):
            return cw._generate_via_lazygophers("prompt", system_prompt="sys")

    def test_happy_path_returns_the_message(self) -> None:
        payload = {"content": [{"type": "text", "text": "feat: 加功能\n"}]}
        self.assertEqual(self._call(mock.Mock(return_value=_resp(payload))),
                         "feat: 加功能")

    def test_request_carries_the_bearer_token_and_url(self) -> None:
        urlopen = mock.Mock(return_value=_resp({"content": []}))
        self._call(urlopen)
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.example.com/v1/chat/compate")
        self.assertEqual(req.headers["Authorization"], "Bearer tok")
        body = json.loads(req.data)
        self.assertTrue(body["disable_thinking"])
        self.assertEqual(body["system"], "sys")

    def test_non_text_blocks_are_ignored(self) -> None:
        payload = {"content": [{"type": "thinking", "text": "内心戏"},
                               {"type": "text", "text": "fix: 修一处"}]}
        self.assertEqual(self._call(mock.Mock(return_value=_resp(payload))), "fix: 修一处")

    def test_http_error_reports_the_status_code(self) -> None:
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))
        r = mock.MagicMock()
        with mock.patch.object(cw, "reporter", return_value=r):
            out = self._call(mock.Mock(side_effect=err))
        self.assertEqual(out, "")
        self.assertIn("429", r.err.call_args[0][0])

    def test_connection_refused_prints_readable_text(self) -> None:
        r = mock.MagicMock()
        with mock.patch.object(cw, "reporter", return_value=r):
            out = self._call(mock.Mock(side_effect=ConnectionRefusedError(61, "Connection refused")))
        self.assertEqual(out, "")
        self.assertIn("Connection refused", r.err.call_args[0][0])
        self.assertNotIn("Errno", r.err.call_args[0][0])

    def test_urlerror_uses_its_reason(self) -> None:
        r = mock.MagicMock()
        with mock.patch.object(cw, "reporter", return_value=r):
            out = self._call(mock.Mock(side_effect=urllib.error.URLError("DNS 查不到")))
        self.assertEqual(out, "")
        self.assertIn("DNS 查不到", r.err.call_args[0][0])

    def test_timeout(self) -> None:
        r = mock.MagicMock()
        with mock.patch.object(cw, "reporter", return_value=r):
            self.assertEqual(self._call(mock.Mock(side_effect=TimeoutError())), "")
        self.assertIn("连接失败", r.err.call_args[0][0])

    def test_malformed_json_is_reported_without_the_exception_name(self) -> None:
        cm = mock.MagicMock()
        cm.__enter__.return_value = SimpleNamespace(read=lambda: b"not json")
        cm.__exit__.return_value = False
        r = mock.MagicMock()
        with mock.patch.object(cw, "reporter", return_value=r):
            self.assertEqual(self._call(mock.Mock(return_value=cm)), "")
        self.assertEqual(r.err.call_args[0][0], "LAZYGOPHERS API 响应格式异常")


class TestDebugDump(unittest.TestCase):
    def test_silent_without_debug(self) -> None:
        r = mock.MagicMock()
        with mock.patch("lib.notify.is_debug", return_value=False), \
             mock.patch.object(cw, "reporter", return_value=r):
            cw._debug_dump("u", {"a": 1}, b"{}")
        r.step.assert_not_called()

    def test_dumps_request_and_response(self) -> None:
        r = mock.MagicMock()
        with mock.patch("lib.notify.is_debug", return_value=True), \
             mock.patch.object(cw, "reporter", return_value=r):
            cw._debug_dump("u", {"a": 1}, b'{"ok":1}')
        self.assertEqual(r.output.call_count, 2)

    def test_missing_response_body(self) -> None:
        r = mock.MagicMock()
        with mock.patch("lib.notify.is_debug", return_value=True), \
             mock.patch.object(cw, "reporter", return_value=r):
            cw._debug_dump("u", {}, None)
        self.assertEqual(r.output.call_args[0][0], "(无)")


class TestHasChanges(unittest.TestCase):
    def test_untracked_only_still_counts(self) -> None:
        outs = {"git diff --cached --name-only": _p(stdout=""),
                "git ls-files": _p(stdout="new.py\n"),
                "git diff --name-only": _p(stdout=""),
                "git status --short": _p(stdout="?? new.py\n")}

        def fake(args, **_kw):
            joined = " ".join(args)
            for k, v in outs.items():
                if joined.startswith(k):
                    return v
            return _p()

        with mock.patch.object(cw, "run", fake):
            has, lines = cw._has_changes()
        self.assertTrue(has)
        self.assertEqual(lines, ["?? new.py"])

    def test_clean_tree(self) -> None:
        with mock.patch.object(cw, "run", return_value=_p(stdout="")):
            self.assertEqual(cw._has_changes(), (False, []))


class TestRunCommitBranches(unittest.TestCase):
    """针对 index.lock 自愈与 API 路径的分支，主路径见 tests/test_commit_wf.py。"""

    def setUp(self) -> None:
        p = mock.patch.object(cw, "_has_changes", return_value=(True, ["M  f.py"]))
        p.start()
        self.addCleanup(p.stop)
        b = mock.patch.object(cw, "current_branch", return_value="master")
        b.start()
        self.addCleanup(b.stop)

    def test_stale_index_lock_is_removed_before_staging(self) -> None:
        calls: list[list[str]] = []

        def fake(args, **_kw):
            calls.append(list(args))
            return _p(stdout="f.py\n")

        with mock.patch.object(cw, "run", fake), \
             mock.patch("os.path.exists", return_value=True):
            self.assertEqual(cw.run_commit("msg", cwd="/repo"), 0)
        self.assertIn(["rm", "-f", "/repo/.git/index.lock"], calls)

    def test_bit_add_retries_once_on_an_index_lock_clash(self) -> None:
        seq = [_p(stdout=""),          # _has_changes 之后的 staged 探测
               _p(returncode=1, stderr="fatal: index.lock exists"),  # bit add 第一次
               _p(returncode=0),       # bit add 第二次
               _p(stdout="f.py\n"),    # 暂存区校验
               _p(returncode=0),       # bit commit
               _p(stdout="abc123\n")]  # rev-parse

        def fake(args, **_kw):
            if args[:3] == ["rm", "-f", ".git/index.lock"]:
                return _p()
            return seq.pop(0) if seq else _p()

        with mock.patch.object(cw, "run", fake), \
             mock.patch("os.path.exists", return_value=False):
            self.assertEqual(cw.run_commit("msg"), 0)
        self.assertEqual(seq, [])

    def test_bit_add_hard_failure_aborts(self) -> None:
        def fake(args, **_kw):
            if args[:2] == ["bit", "add"]:
                return _p(returncode=1, stderr="permission denied")
            return _p(stdout="")

        with mock.patch.object(cw, "run", fake), \
             mock.patch("os.path.exists", return_value=False):
            self.assertEqual(cw.run_commit("msg"), 1)

    def test_empty_index_after_bit_add_falls_back_to_git_add(self) -> None:
        calls: list[list[str]] = []

        def fake(args, **_kw):
            calls.append(list(args))
            if args[:2] == ["git", "rev-parse"]:
                return _p(stdout="abc123\n")
            return _p(stdout="")

        with mock.patch.object(cw, "run", fake), \
             mock.patch("os.path.exists", return_value=False):
            self.assertEqual(cw.run_commit("msg"), 0)
        self.assertIn(["git", "add", "-A"], calls)

    def test_api_path_is_used_when_the_env_is_set(self) -> None:
        with mock.patch.object(cw, "run", return_value=_p(stdout="f.py\n")), \
             mock.patch("os.path.exists", return_value=False), \
             mock.patch.dict(cw.os.environ, ENV), \
             mock.patch.object(cw, "_generate_via_lazygophers",
                               return_value="feat: 来自 API") as gen, \
             mock.patch.object(cw, "generate_via_claude") as claude:
            self.assertEqual(cw.run_commit(), 0)
        gen.assert_called_once()
        claude.assert_not_called()

    def test_commit_failure_cleans_the_lock_and_gives_up_after_three_tries(self) -> None:
        calls: list[list[str]] = []

        def fake(args, **_kw):
            calls.append(list(args))
            if args[:2] == ["bit", "commit"]:
                return _p(returncode=1, stderr="fatal: index.lock exists")
            return _p(stdout="f.py\n")

        with mock.patch.object(cw, "run", fake), \
             mock.patch("os.path.exists", return_value=False):
            self.assertEqual(cw.run_commit("msg"), 1)
        commits = [c for c in calls if c[:2] == ["bit", "commit"]]
        self.assertEqual(len(commits), 3)
        self.assertIn(["rm", "-f", ".git/index.lock"], calls)


class TestBuildPromptTruncation(unittest.TestCase):
    def test_long_diff_is_cut_at_a_line_boundary(self) -> None:
        diff = "\n".join(f"+line {i}" for i in range(2000))
        with mock.patch.object(cw, "run", return_value=_p(stdout=diff)):
            prompt = cw._build_prompt(["M  f.py"])
        self.assertIn("（diff 已截断）", prompt)
        self.assertLess(len(prompt), len(diff))

    def test_empty_index_is_labelled(self) -> None:
        with mock.patch.object(cw, "run", return_value=_p(stdout="")):
            self.assertIn("（暂存区空）", cw._build_prompt(["M  f.py"]))


class TestCommitAll(unittest.TestCase):
    """只验证传给 BatchRunner 的 operation 回调，不真的扫盘。"""

    def _run(self, run_commit_rc=0, has_changes=True, **kw):
        from lib.batch_git import BatchResult, RepoPlan
        captured = {}

        class FakeRunner:
            def run(self, op):
                captured["op"] = op
                return BatchResult(total=1, succeeded=[], skipped=[], failed=[])

        with mock.patch("lib.batch_git.BatchRunner", FakeRunner), \
             mock.patch.object(cw, "_has_changes", return_value=(has_changes, [])), \
             mock.patch.object(cw, "run_commit", return_value=run_commit_rc), \
             mock.patch.object(cw, "reporter", return_value=mock.MagicMock()):
            rc = cw.commit_all(".", **kw)
            plan: RepoPlan = captured["op"].detect_fn(pathlib.Path("/repo"),
                                                     mock.MagicMock(), pathlib.Path("/"))
        return rc, plan, captured["op"]

    def test_clean_repo_is_skipped(self) -> None:
        _, plan, _ = self._run(has_changes=False)
        self.assertEqual((plan.status, plan.detail), ("skip", "无变更"))

    def test_successful_commit(self) -> None:
        _, plan, _ = self._run()
        self.assertEqual((plan.status, plan.detail), ("ok", "已提交"))

    def test_dry_run_is_labelled(self) -> None:
        _, plan, _ = self._run(dry_run=True)
        self.assertEqual(plan.detail, "演练")

    def test_failure_carries_the_exit_code(self) -> None:
        _, plan, _ = self._run(run_commit_rc=3)
        self.assertEqual((plan.status, plan.detail), ("fail", "退出码 3"))

    def test_returns_zero_when_nothing_failed(self) -> None:
        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

    def test_confirm_flag_is_passed_through(self) -> None:
        _, _, op = self._run(confirm=True)
        self.assertTrue(op.confirm)

    def test_failed_repos_make_the_batch_fail(self) -> None:
        from lib.batch_git import BatchResult

        class FakeRunner:
            def run(self, op):
                return BatchResult(total=1, succeeded=[], skipped=[],
                                   failed=[SimpleNamespace(name="r", status="fail", detail="x")])

        with mock.patch("lib.batch_git.BatchRunner", FakeRunner), \
             mock.patch.object(cw, "reporter", return_value=mock.MagicMock()):
            self.assertEqual(cw.commit_all("."), 1)


if __name__ == "__main__":
    unittest.main()
