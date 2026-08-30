#!/usr/bin/env python3
"""Tests for lib.exec."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.exec as exec_mod


class TestShellJoin(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(exec_mod.shell_join(["echo", "a b"]), "echo 'a b'")

    def test_single(self):
        self.assertEqual(exec_mod.shell_join(["echo"]), "echo")

    def test_empty(self):
        self.assertEqual(exec_mod.shell_join([]), "")

    def test_non_string_fallback(self):
        # shlex.join 抛异常时回退 " ".join; 回退仍需 str 元素
        # 构造让 shlex.join 失败但 join 可工作的场景难以直接构造,
        # 此处验证回退分支可被触发: 用 monkeypatch
        with patch.object(exec_mod.shlex, "join", side_effect=ValueError("x")):
            self.assertEqual(exec_mod.shell_join(["a", "b"]), "a b")


class TestRun(unittest.TestCase):
    def test_success(self):
        p = exec_mod.run(["echo", "hello"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("hello", p.stdout)

    def test_failure_no_check(self):
        p = exec_mod.run(["false"])
        self.assertNotEqual(p.returncode, 0)

    def test_check_raises(self):
        with self.assertRaises(Exception):
            exec_mod.run(["false"], check=True)

    def test_cwd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = exec_mod.run(["pwd"], cwd=td)
            self.assertEqual(p.returncode, 0)
            self.assertIn(td, p.stdout)

    def test_capture(self):
        p = exec_mod.run(["echo", "x"])
        self.assertEqual(p.stdout.strip(), "x")

    def test_timeout_raises(self):
        with self.assertRaises(exec_mod.CommandTimeout):
            exec_mod.run(["sleep", "5"], timeout=1)

    def test_timeout_kills_proc_group(self):
        """超时后子进程不留残留。"""
        import subprocess
        try:
            exec_mod.run(["sleep", "10"], timeout=1)
        except exec_mod.CommandTimeout:
            pass
        r = subprocess.run(["pgrep", "-f", "sleep 10"], capture_output=True)
        self.assertEqual(r.stdout.strip(), b"", "sleep 子进程未被清理")


class TestRunNoCapture(unittest.TestCase):
    def test_success(self):
        rc = exec_mod.run_no_capture(["true"])
        self.assertEqual(rc, 0)

    def test_failure(self):
        rc = exec_mod.run_no_capture(["false"])
        self.assertNotEqual(rc, 0)


class TestRunLogged(unittest.TestCase):
    def test_with_reporter_success(self):
        r = MagicMock()
        p = exec_mod.run_logged(["echo", "hi"], r=r, title="t")
        self.assertEqual(p.returncode, 0)
        r.step.assert_called()
        # 成功且 show_output_on_success=False → 不输出
        r.output.assert_not_called()

    def test_no_reporter(self):
        p = exec_mod.run_logged(["echo", "hi"])
        self.assertEqual(p.returncode, 0)

    def test_check_raises_on_fail(self):
        r = MagicMock()
        import subprocess
        with self.assertRaises(subprocess.CalledProcessError):
            exec_mod.run_logged(["false"], check=True, r=r)

    def test_failure_shows_output(self):
        r = MagicMock()
        exec_mod.run_logged(["sh", "-c", "echo err >&2; exit 1"], r=r)
        r.cmd_result.assert_called_once()
        call_kwargs = r.cmd_result.call_args[1]
        self.assertNotEqual(call_kwargs["returncode"], 0)


class TestLooksLikeNetworkError(unittest.TestCase):
    def test_network(self):
        self.assertTrue(exec_mod.looks_like_network_error("network unreachable"))

    def test_timeout(self):
        self.assertTrue(exec_mod.looks_like_network_error("fatal: timeout"))

    def test_connection(self):
        self.assertTrue(exec_mod.looks_like_network_error("connection refused"))

    def test_clean(self):
        self.assertFalse(exec_mod.looks_like_network_error("syntax error"))

    def test_empty(self):
        self.assertFalse(exec_mod.looks_like_network_error(""))


class TestRetryCommand(unittest.TestCase):
    @patch("lib.exec.run")
    def test_success_first_try(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        res = exec_mod.retry_command(["git", "fetch"])
        self.assertTrue(res.ok)
        self.assertEqual(res.attempts, 1)
        self.assertIn("ok", res.last_output)

    @patch("lib.exec.run")
    def test_non_network_error_no_retry(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="syntax error", stderr="")
        res = exec_mod.retry_command(["make"], max_retries=3)
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 1)

    @patch("lib.exec.run")
    def test_network_error_exhausts_retries(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="connection timeout", stderr="")
        with patch("time.sleep"):
            res = exec_mod.retry_command(["git", "fetch"], max_retries=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 3)  # 1 initial + 2 retries

    @patch("lib.exec.run")
    def test_timeout_treated_as_retryable(self, mock_run):
        """超时视为网络错误，触发重试。"""
        mock_run.side_effect = exec_mod.CommandTimeout("timeout")
        with patch("time.sleep"):
            res = exec_mod.retry_command(["git", "push"], max_retries=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 3)


class TestRetryResultDataclass(unittest.TestCase):
    def test_fields(self):
        r = exec_mod.RetryResult(ok=True, attempts=1, last_output="x")
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 1)
        self.assertEqual(r.last_output, "x")


class TestKeyboardInterrupt(unittest.TestCase):
    def test_run_wraps_interrupt(self):
        with patch("subprocess.run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt) as cm:
                exec_mod.run(["echo", "x"])
        self.assertIn("被用户中断", str(cm.exception))

    def test_run_no_capture_terminates_on_interrupt(self):
        proc = MagicMock()
        proc.wait.side_effect = [KeyboardInterrupt, 0]
        with patch("subprocess.Popen", return_value=proc):
            with self.assertRaises(KeyboardInterrupt) as cm:
                exec_mod.run_no_capture(["sleep", "9"])
        self.assertIn("被用户中断", str(cm.exception))
        proc.terminate.assert_called_once()

    def test_run_no_capture_interrupt_before_popen(self):
        with patch("subprocess.Popen", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                exec_mod.run_no_capture(["sleep", "9"])


class TestRunNoCaptureTimeout(unittest.TestCase):
    def test_timeout_kills_group_and_raises(self):
        import subprocess

        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
        with patch("subprocess.Popen", return_value=proc), \
             patch.object(exec_mod, "_kill_proc_group") as mock_kill:
            with self.assertRaises(exec_mod.CommandTimeout) as cm:
                exec_mod.run_no_capture(["sleep", "9"], timeout=1)
        self.assertIn("命令超时", str(cm.exception))
        mock_kill.assert_called_once_with(proc)

    def test_real_timeout_leaves_no_child(self):
        import subprocess

        with self.assertRaises(exec_mod.CommandTimeout):
            exec_mod.run_no_capture(["sleep", "11"], timeout=1)
        r = subprocess.run(["pgrep", "-f", "sleep 11"], capture_output=True)
        self.assertEqual(r.stdout.strip(), b"", "sleep 子进程未被清理")


class TestKillProcGroup(unittest.TestCase):
    def test_kills_group(self):
        proc = MagicMock(pid=4242)
        with patch("os.getpgid", return_value=4242), patch("os.killpg") as mock_killpg:
            exec_mod._kill_proc_group(proc)
        mock_killpg.assert_called_once()
        proc.kill.assert_not_called()

    def test_falls_back_to_kill(self):
        proc = MagicMock(pid=4242)
        with patch("os.getpgid", side_effect=ProcessLookupError):
            exec_mod._kill_proc_group(proc)
        proc.kill.assert_called_once()

    def test_oserror_falls_back(self):
        proc = MagicMock(pid=4242)
        with patch("os.getpgid", return_value=4242), \
             patch("os.killpg", side_effect=OSError):
            exec_mod._kill_proc_group(proc)
        proc.kill.assert_called_once()


class TestDebugLog(unittest.TestCase):
    def setUp(self):
        import lib.notify as notify_mod
        self._notify = notify_mod
        self._prev = notify_mod._DEBUG

    def tearDown(self):
        self._notify._DEBUG = self._prev

    def test_disabled_is_noop(self):
        self._notify._DEBUG = False
        with patch("lib.ui.reporter") as mock_reporter:
            exec_mod._debug_log(None, ["echo"], None, 0.1)
        mock_reporter.assert_not_called()

    def test_logs_cmd_rc_elapsed(self):
        self._notify._DEBUG = True
        r = MagicMock()
        with patch("lib.ui.reporter", return_value=r):
            p = MagicMock(returncode=0, stdout="out", stderr="")
            exec_mod._debug_log(p, ["echo", "hi"], "/repo", 1.5)
        msg = r.step.call_args[0][0]
        self.assertIn("cwd=/repo", msg)
        self.assertIn("rc=0", msg)
        self.assertIn("1.50s", msg)
        r.output.assert_called_once_with("out")

    def test_no_proc_uses_explicit_rc(self):
        self._notify._DEBUG = True
        r = MagicMock()
        with patch("lib.ui.reporter", return_value=r):
            exec_mod._debug_log(None, ["sleep"], None, 0.2, rc=7)
        self.assertIn("rc=7", r.step.call_args[0][0])
        r.output.assert_not_called()

    def test_no_proc_no_rc_shows_question_mark(self):
        self._notify._DEBUG = True
        r = MagicMock()
        with patch("lib.ui.reporter", return_value=r):
            exec_mod._debug_log(None, ["sleep"], None, 0.2)
        self.assertIn("rc=?", r.step.call_args[0][0])

    def test_blank_output_not_printed(self):
        self._notify._DEBUG = True
        r = MagicMock()
        with patch("lib.ui.reporter", return_value=r):
            p = MagicMock(returncode=0, stdout="  ", stderr="")
            exec_mod._debug_log(p, ["echo"], None, 0.1)
        r.output.assert_not_called()

    def test_reporter_import_failure_is_swallowed(self):
        self._notify._DEBUG = True
        import builtins

        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "lib.ui":
                raise ImportError("no rich")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", boom):
            exec_mod._debug_log(None, ["echo"], None, 0.1)


class TestPropagateDebugEnv(unittest.TestCase):
    def setUp(self):
        import lib.notify as notify_mod
        self._notify = notify_mod
        self._prev = notify_mod._DEBUG

    def tearDown(self):
        self._notify._DEBUG = self._prev

    def test_non_debug_returns_unchanged(self):
        self._notify._DEBUG = False
        self.assertIsNone(exec_mod._propagate_debug_env(None))
        env = {"A": "1"}
        self.assertIs(exec_mod._propagate_debug_env(env), env)

    def test_debug_injects_into_explicit_env(self):
        self._notify._DEBUG = True
        out = exec_mod._propagate_debug_env({"A": "1"})
        self.assertEqual(out["A"], "1")
        self.assertEqual(out["SCRIPTS_DEBUG"], "1")

    def test_debug_copies_os_environ_when_none(self):
        self._notify._DEBUG = True
        out = exec_mod._propagate_debug_env(None)
        self.assertEqual(out["SCRIPTS_DEBUG"], "1")
        self.assertIn("PATH", out)


if __name__ == "__main__":
    unittest.main()
