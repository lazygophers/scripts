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
        original = exec_mod.shlex.join
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


class TestRetryResultDataclass(unittest.TestCase):
    def test_fields(self):
        r = exec_mod.RetryResult(ok=True, attempts=1, last_output="x")
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 1)
        self.assertEqual(r.last_output, "x")


if __name__ == "__main__":
    unittest.main()
