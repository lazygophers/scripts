"""live_server CLI 薄壳：参数组装与两条快速失败路径。

逻辑层（serve/上传/SSE/TLS）在 test_live_server.py 用 in-process 真实 HTTP 测；
这里只测 lib/cli/live_server.py 的 main() 把 argv 正确折成 Config、
目录不存在和 cert/key 半给时拦下来。
"""
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import lib.live_server
from lib.cli import live_server as cli


class TestLiveServerCli(unittest.TestCase):
    def _run(self, argv):
        err = io.StringIO()
        with redirect_stderr(err):
            with mock.patch.object(lib.live_server, "run", return_value=0) as run:
                rc = cli.main(["live_server", *argv])
        return rc, run, err.getvalue()

    def test_main_folds_argv_into_config(self):
        with TemporaryDirectory() as tmp:
            rc, run, err = self._run(
                [tmp, "--host", "0.0.0.0", "--port", "8080", "--spa", "--no-open"])
            self.assertEqual(rc, 0)
            cfg = run.call_args[0][0]
            self.assertEqual(cfg.root, Path(tmp).resolve())
            self.assertEqual(cfg.host, "0.0.0.0")
            self.assertEqual(cfg.port, 8080)
            self.assertTrue(cfg.spa)
            self.assertFalse(cfg.open_browser)

    def test_auth_user_pass_without_tls_warns(self):
        with TemporaryDirectory() as tmp:
            rc, run, err = self._run([tmp, "--auth", "u:p", "--no-open"])
            self.assertEqual(rc, 0)
            cfg = run.call_args[0][0]
            self.assertEqual(cfg.auth, ("u", "p"))
            self.assertIn("WARN", err)

    def test_tls_and_cert_passthrough(self):
        with TemporaryDirectory() as tmp:
            rc, run, _ = self._run(
                [tmp, "--tls", "--cert", "c.pem", "--key", "k.pem", "--no-open"])
            self.assertEqual(rc, 0)
            cfg = run.call_args[0][0]
            self.assertTrue(cfg.tls)
            self.assertEqual((cfg.cert, cfg.key), (Path("c.pem"), Path("k.pem")))

    def test_missing_dir_fails_fast(self):
        rc, run, err = self._run(["/nonexistent-dir-xyz", "--no-open"])
        self.assertEqual(rc, 2)
        run.assert_not_called()
        self.assertIn("ERROR", err)

    def test_cert_without_key_rejected(self):
        with TemporaryDirectory() as tmp:
            rc, run, err = self._run([tmp, "--cert", "c.pem", "--no-open"])
            self.assertEqual(rc, 2)
            run.assert_not_called()
            self.assertIn("--cert 和 --key", err)
