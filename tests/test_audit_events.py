"""日志缺口补齐的回归测试（2026-09-23 审计票）。

覆盖：exec.fail（含命令行打码）、config.write、ovpn.route（host/network/flush）、
ovpn.resolver（clean/write）、email.login、Reporter.err 汇聚的 cli.error
（webgrab 抓取失败）。统一 patch lib.log.record 断言事件字段，不写真日志文件。
"""
from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _events(record_mock):
    """把 record.call_args_list 拍平成 [{event: ..., **fields}]。"""
    return [{**c.kwargs, "event": c.args[0]} for c in record_mock.call_args_list]


class TestExecFail(unittest.TestCase):
    def test_fail_event_carries_rc_and_stderr(self):
        from lib import exec as ex

        with mock.patch("lib.log.record") as rec:
            p = ex.run(["/bin/sh", "-c", "echo boom >&2; exit 3"],
                       capture_output=True, check=False)
        self.assertEqual(p.returncode, 3)
        evs = _events(rec)
        self.assertEqual(len(evs), 1, "失败恰好一条，成功不记")
        self.assertEqual(evs[0]["event"], "exec.fail")
        self.assertEqual(evs[0]["rc"], 3)
        self.assertIn("boom", evs[0]["stderr"])

    def test_success_records_nothing(self):
        from lib import exec as ex

        with mock.patch("lib.log.record") as rec:
            ex.run(["/usr/bin/true"], capture_output=True)
        rec.assert_not_called()

    def test_secret_flag_value_redacted(self):
        from lib.exec import _scrub_secrets

        scrubbed = _scrub_secrets(["curl", "--password", "hunter2", "https://x"])
        self.assertNotIn("hunter2", scrubbed)
        self.assertIn("<REDACTED>", scrubbed)

    def test_plain_args_survive(self):
        from lib.exec import _scrub_secrets

        cmd = ["git", "push", "origin", "main"]
        self.assertEqual(_scrub_secrets(cmd), "git push origin main")


class TestConfigWrite(unittest.TestCase):
    def test_save_records_path_and_profiles(self):
        from lib.profile_store import ProfileStore

        with tempfile.TemporaryDirectory() as td:
            store = ProfileStore(
                "testtool.yaml", error=RuntimeError, key_fn=lambda k: k,
                tool="testtool", path_resolver=lambda: pathlib.Path(td) / "testtool.yaml")
            with mock.patch("lib.log.record") as rec:
                store.save({"profiles": {"a.com": {"token": "x"}, "b.com": {}}})
            evs = _events(rec)
            self.assertEqual(evs[0]["event"], "config.write")
            self.assertEqual(evs[0]["logger"], "testtool")
            self.assertEqual(evs[0]["keys"], ["a.com", "b.com"])
            self.assertTrue(evs[0]["path"].endswith("testtool.yaml"))


class TestOvpnRouteEvents(unittest.TestCase):
    """RouteTable add_host / add_network / flush 的事件（lib/ovpn_split.py）。"""

    def _table(self):
        from lib import ovpn_split as S

        return S, S.RouteTable("utun4")

    def test_add_host_success(self):
        S, t = self._table()
        with mock.patch.object(S.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch("lib.log.record") as rec:
            self.assertTrue(t.add_host("203.0.113.7"))
        evs = _events(rec)
        self.assertEqual(evs[0]["event"], "ovpn.route")
        self.assertEqual(evs[0]["action"], "add")
        self.assertEqual(evs[0]["kind"], "host")
        self.assertEqual(evs[0]["target"], "203.0.113.7")
        self.assertTrue(evs[0]["ok"])

    def test_add_host_failure(self):
        S, t = self._table()
        with mock.patch.object(S.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stderr="Network is unreachable")), \
             mock.patch("lib.log.record") as rec:
            self.assertFalse(t.add_host("203.0.113.7"))
        evs = _events(rec)
        self.assertEqual(evs[0]["event"], "ovpn.route")
        self.assertFalse(evs[0]["ok"])
        self.assertIn("unreachable", evs[0]["error"])

    def test_add_network_success_and_invalid(self):
        S, t = self._table()
        with mock.patch.object(S.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch("lib.log.record") as rec:
            self.assertTrue(t.add_network("10.8.0.0/16"))
        evs = _events(rec)
        self.assertEqual(evs[0]["kind"], "network")
        self.assertEqual(evs[0]["target"], "10.8.0.0/16")
        self.assertTrue(evs[0]["ok"])

        with mock.patch.object(S.subprocess, "run"), \
             mock.patch("lib.log.record") as rec:
            self.assertFalse(t.add_network("not-a-cidr"))
        evs = _events(rec)
        self.assertEqual(evs[0]["event"], "ovpn.route")
        self.assertFalse(evs[0]["ok"])
        self.assertEqual(evs[0]["error"], "invalid CIDR")

    def test_flush_records_deletes(self):
        S, t = self._table()
        t.added = {"203.0.113.7", "10.8.0.0/16"}
        with mock.patch.object(S.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch("lib.log.record") as rec:
            t.flush()
        evs = _events(rec)
        deletes = {(e["kind"], e["target"]) for e in evs if e["action"] == "delete"}
        self.assertEqual(deletes, {("host", "203.0.113.7"), ("network", "10.8.0.0/16")})
        self.assertTrue(all(e["ok"] for e in evs))


class TestOvpnResolverEvents(unittest.TestCase):
    def test_clean_records_count(self):
        from lib import ovpn_split as S

        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            (base / "example.com").write_text(S.resolver_file_content(5354))
            with mock.patch.object(S.subprocess, "run",
                                   return_value=mock.Mock(returncode=0, stderr="")), \
                 mock.patch("lib.log.record") as rec:
                S.clean_resolver_files(None, base)
            evs = _events(rec)
            self.assertEqual(evs[0]["event"], "ovpn.resolver")
            self.assertEqual(evs[0]["action"], "clean")
            self.assertEqual(evs[0]["count"], 1)
            self.assertTrue(evs[0]["ok"])

    def test_clean_noop(self):
        from lib import ovpn_split as S

        with tempfile.TemporaryDirectory() as td, mock.patch("lib.log.record") as rec:
            S.clean_resolver_files(None, pathlib.Path(td))
        evs = _events(rec)
        self.assertEqual(evs[0]["count"], 0)
        self.assertTrue(evs[0]["ok"])

    def test_write_records_written_and_failed(self):
        from lib import ovpn_split as S

        def fake_run(cmd, **kw):
            name = cmd[-1].rsplit("/", 1)[-1]
            rc = 1 if name == "bad.com" else 0
            return mock.Mock(returncode=rc, stderr="denied" if rc else "")

        with mock.patch.object(S.subprocess, "run", side_effect=fake_run), \
             mock.patch("lib.log.record") as rec:
            S.write_resolver_files(["a.com", "bad.com"], 5354, None,
                                   pathlib.Path("/etc/resolver"))
        evs = _events(rec)
        self.assertEqual(evs[0]["event"], "ovpn.resolver")
        self.assertEqual(evs[0]["action"], "write")
        self.assertFalse(evs[0]["ok"])
        self.assertEqual(evs[0]["written"], ["/etc/resolver/a.com"])
        self.assertEqual(evs[0]["failed"], ["/etc/resolver/bad.com"])


class TestEmailLoginEvent(unittest.TestCase):
    def test_verify_records_outcome(self):
        import lib.email as mail
        import lib.cli.email as cli_mod
        from lib.cli.email import EmailCli

        cli = EmailCli()
        saved: list[dict] = []
        orig = (cli_mod.load_config, cli_mod.save_config, cli_mod.config_lock)

        class _null_ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _restore():
            (cli_mod.load_config, cli_mod.save_config,
             cli_mod.config_lock) = orig

        cli_mod.load_config = lambda *a, **kw: dict(saved[-1]) if saved else {}
        cli_mod.save_config = lambda cfg, *a, **kw: saved.append(dict(cfg))
        cli_mod.config_lock = lambda *a, **kw: _null_ctx()
        self.addCleanup(_restore)
        with mock.patch.object(mail, "check", return_value=(True, "都登录成功")), \
             mock.patch("lib.log.record") as rec:
            rc = cli._verify_then_save("me@qq.com", _fake_profile())
        self.assertEqual(rc, 0)
        evs = [e for e in _events(rec) if e["event"] == "email.login"]
        self.assertEqual(len(evs), 1)
        self.assertTrue(evs[0]["ok"])
        self.assertEqual(evs[0]["address"], "me@qq.com")


def _fake_profile() -> dict:
    return {
        "address": "me@qq.com",
        "name": "me",
        "auth_code": "x" * 16,
        "imap": {"host": "imap.qq.com", "port": 993},
        "smtp": {"host": "smtp.qq.com", "port": 465, "mode": "ssl"},
    }


class TestReporterErrSink(unittest.TestCase):
    def test_webgrab_failure_reaches_cliError(self):
        from lib import webgrab

        err = io.StringIO()
        direct = mock.patch.object(webgrab, "fetch_direct", return_value=(403, "nope"))
        render = mock.patch.object(webgrab, "fetch_render", return_value="Verify you are human")
        with direct, render, redirect_stderr(err), \
             mock.patch("lib.log.record") as rec:
            rc = webgrab.main(["webgrab", "https://example.com"])
        self.assertEqual(rc, 1)
        evs = _events(rec)
        self.assertEqual(evs[0]["event"], "cli.error")
        self.assertIn("抓取失败", evs[0]["msg"])
        self.assertIn("example.com", evs[0]["msg"])


if __name__ == "__main__":
    unittest.main()
