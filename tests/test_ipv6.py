"""ipv6 的 enable / disable 子命令测试。

跑真正的 CLI 方法（`Ipv6Cli.enable` / `.disable`），断言 networksetup 调用序列、
sudo 校验、服务名解析（首行说明文字要跳过，`*` 前缀要剥掉）。所有外部命令都是假的。
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.cli import ipv6 as iv

SERVICES_OUT = (
    "An asterisk (*) denotes that a network service is disabled.\n"
    "Wi-Fi\n"
    "*Tailscale\n"
)


def _completed(returncode: int = 0, stdout: str = "") -> mock.Mock:
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    return proc


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = iv.Ipv6Cli()
        self.cli._r = mock.MagicMock()

    def _call(self, method):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = method()
        return rc, buf.getvalue()


class TestServices(unittest.TestCase):
    def test_skips_header_and_strips_disabled_marker(self) -> None:
        with mock.patch.object(iv.subprocess, "run", return_value=_completed(stdout=SERVICES_OUT)):
            self.assertEqual(iv._services(), ["Wi-Fi", "Tailscale"])

    def test_blank_lines_are_dropped(self) -> None:
        out = "header\nWi-Fi\n\n\n"
        with mock.patch.object(iv.subprocess, "run", return_value=_completed(stdout=out)):
            self.assertEqual(iv._services(), ["Wi-Fi"])


class TestSudoGate(CliCase):
    def test_disable_without_sudo_fails_without_touching_network(self) -> None:
        with mock.patch.object(iv.os, "geteuid", return_value=501), \
             mock.patch.object(iv.subprocess, "run") as run:
            rc, _ = self._call(self.cli.disable)
        self.assertEqual(rc, 1)
        run.assert_not_called()
        self.cli._r.err.assert_called_once()

    def test_enable_without_sudo_fails(self) -> None:
        with mock.patch.object(iv.os, "geteuid", return_value=501):
            rc, _ = self._call(self.cli.enable)
        self.assertEqual(rc, 1)


class TestApply(CliCase):
    def _run_as_root(self, method, service_run_results):
        """第一次 networksetup 调用返回服务列表，后续按顺序消费 service_run_results。"""
        calls: list[list[str]] = []
        results = iter(service_run_results)

        def run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "-listallnetworkservices":
                return _completed(stdout=SERVICES_OUT)
            return _completed(returncode=next(results))

        with mock.patch.object(iv.os, "geteuid", return_value=0), \
             mock.patch.object(iv.subprocess, "run", side_effect=run):
            rc, out = self._call(method)
        return rc, out, calls

    def test_disable_calls_setv6off_for_every_service(self) -> None:
        rc, out, calls = self._run_as_root(self.cli.disable, [0, 0])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[1], ["networksetup", "-setv6off", "Wi-Fi"])
        self.assertEqual(calls[2], ["networksetup", "-setv6off", "Tailscale"])
        self.assertIn("off: Wi-Fi", out)
        self.assertIn("off: Tailscale", out)

    def test_enable_calls_setv6automatic(self) -> None:
        rc, out, calls = self._run_as_root(self.cli.enable, [0, 0])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[1], ["networksetup", "-setv6automatic", "Wi-Fi"])
        self.assertIn("on: Wi-Fi", out)

    def test_failed_service_is_reported_as_skip_but_command_still_succeeds(self) -> None:
        rc, out, _ = self._run_as_root(self.cli.disable, [0, 1])
        self.assertEqual(rc, 0)
        self.assertIn("off: Wi-Fi", out)
        self.assertIn("skip: Tailscale", out)


if __name__ == "__main__":
    unittest.main()
