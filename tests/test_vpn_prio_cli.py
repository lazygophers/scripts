"""vpn-prio 的 status / apply / reset 三个子命令测试。

tests/test_vpn_prio.py 覆盖的是解析函数；这里跑真正的 CLI 方法，断言
networksetup 是否被调用、dry-run 是否只打印不执行。所有外部命令都是假的。
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

vp = importlib.machinery.SourceFileLoader(
    "vp_cli_under_test",
    str(Path(__file__).resolve().parent.parent / "bin" / "vpn-prio"),
).load_module()


SERVICES_OUT = (
    "(1) Tailscale\n"
    "(2) Wi-Fi\n"
    "(3) USB 10/100/1000 LAN\n"
)


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = vp.VpnPrioCli()
        self.cli._r = mock.MagicMock()
        env = mock.patch.dict(os.environ, {"SCRIPTS_DRY_RUN": "0"})
        env.start()
        self.addCleanup(env.stop)

    def _call(self, method, run_fn):
        """跑一个子命令，返回 (退出码, stdout 文本)。"""
        buf = io.StringIO()
        with mock.patch.object(vp, "_run", run_fn), \
             contextlib.redirect_stdout(buf), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = method()
        return rc, buf.getvalue()


class TestRun(unittest.TestCase):
    def test_missing_binary_becomes_rc_1(self) -> None:
        with mock.patch.object(vp.subprocess, "run", side_effect=FileNotFoundError("没有该命令")):
            rc, out, err = vp._run(["nope"])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("没有该命令", err)

    def test_timeout_becomes_rc_1(self) -> None:
        with mock.patch.object(vp.subprocess, "run",
                               side_effect=vp.subprocess.TimeoutExpired("networksetup", 10)):
            rc, _, err = vp._run(["networksetup"])
        self.assertEqual(rc, 1)
        self.assertIn("networksetup", err)

    def test_normal_output_is_passed_through(self) -> None:
        done = vp.subprocess.CompletedProcess([], 0, "输出", "报错")
        with mock.patch.object(vp.subprocess, "run", return_value=done):
            self.assertEqual(vp._run(["x"]), (0, "输出", "报错"))


class TestStatus(CliCase):
    def _fake(self, *, services=SERVICES_OUT, pgrep="", netstat=""):
        def run(cmd):
            if cmd[0] == "networksetup":
                return 0, services, ""
            if cmd[0] == "pgrep":
                return 0, pgrep, ""
            return 0, netstat, ""
        return run

    def test_lists_services_and_the_target_order(self) -> None:
        rc, out = self._call(self.cli.status, self._fake())
        self.assertEqual(rc, 0)
        self.assertIn("1. Tailscale", out)
        self.assertIn("★ 1. USB 10/100/1000 LAN", out)

    def test_no_services_is_reported(self) -> None:
        rc, out = self._call(self.cli.status, self._fake(services=""))
        self.assertEqual(rc, 0)
        self.assertIn("(无 service)", out)

    def test_running_openvpn_shows_pids(self) -> None:
        line = "577 /Library/Frameworks/OpenVPNConnect.framework/Versions/A/usr/sbin/ovpnagent\n"
        _, out = self._call(self.cli.status, self._fake(pgrep=line))
        self.assertIn("pids=577", out)
        self.assertIn("ovpnagent", out)

    def test_stopped_openvpn(self) -> None:
        _, out = self._call(self.cli.status, self._fake())
        self.assertIn("✗ 未运行", out)

    def test_active_bridge100_gets_a_warning(self) -> None:
        netstat = "default   192.168.1.1   UGScg   bridge100\n"
        _, out = self._call(self.cli.status, self._fake(netstat=netstat))
        self.assertIn("⚠ active", out)
        self.assertIn("竞争 default 出口", out)

    def test_rejected_bridge100_is_fine(self) -> None:
        netstat = "default   link#23   UCSIg   bridge100   !\n"
        _, out = self._call(self.cli.status, self._fake(netstat=netstat))
        self.assertIn("✓ rejected", out)


class TestApply(CliCase):
    def test_no_services_fails(self) -> None:
        rc, _ = self._call(self.cli.apply, lambda cmd: (0, "", ""))
        self.assertEqual(rc, 1)

    def test_already_ordered_is_a_noop(self) -> None:
        ordered = "(1) USB 10/100/1000 LAN\n(2) Wi-Fi\n(3) Tailscale\n"
        calls: list[list[str]] = []

        def run(cmd):
            calls.append(cmd)
            return 0, ordered, ""

        rc, out = self._call(self.cli.apply, run)
        self.assertEqual(rc, 0)
        self.assertIn("无需调整", out)
        self.assertEqual(len(calls), 1)  # 只列表，不重排

    def test_reorders_and_reports_the_caveats(self) -> None:
        calls: list[list[str]] = []

        def run(cmd):
            calls.append(cmd)
            return 0, SERVICES_OUT if cmd[1] == "-listnetworkserviceorder" else "", ""

        rc, out = self._call(self.cli.apply, run)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[1][:2], ["networksetup", "-ordernetworkservices"])
        self.assertEqual(calls[1][2], "USB 10/100/1000 LAN")
        self.assertIn("已调整", out)
        self.assertIn("redirect-gateway", out)

    def test_dry_run_only_prints(self) -> None:
        with mock.patch.dict(os.environ, {"SCRIPTS_DRY_RUN": "1"}):
            rc, out = self._call(self.cli.apply,
                                 lambda cmd: (0, SERVICES_OUT if cmd[1] == "-listnetworkserviceorder" else "", ""))
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run] would run", out)

    def test_networksetup_failure_is_propagated(self) -> None:
        def run(cmd):
            if cmd[1] == "-listnetworkserviceorder":
                return 0, SERVICES_OUT, ""
            return 3, "", "权限不足"

        rc, _ = self._call(self.cli.apply, run)
        self.assertEqual(rc, 3)


class TestReset(CliCase):
    def test_no_services_fails(self) -> None:
        rc, _ = self._call(self.cli.reset, lambda cmd: (0, "", ""))
        self.assertEqual(rc, 1)

    def test_restores_alphabetical_order(self) -> None:
        calls: list[list[str]] = []

        def run(cmd):
            calls.append(cmd)
            return 0, SERVICES_OUT if cmd[1] == "-listnetworkserviceorder" else "", ""

        rc, out = self._call(self.cli.reset, run)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[1][2:],
                         ["Tailscale", "USB 10/100/1000 LAN", "Wi-Fi"])
        self.assertIn("已还原", out)

    def test_dry_run_only_prints(self) -> None:
        with mock.patch.dict(os.environ, {"SCRIPTS_DRY_RUN": "1"}):
            rc, out = self._call(self.cli.reset,
                                 lambda cmd: (0, SERVICES_OUT if cmd[1] == "-listnetworkserviceorder" else "", ""))
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run] would run", out)

    def test_failure_is_propagated(self) -> None:
        def run(cmd):
            if cmd[1] == "-listnetworkserviceorder":
                return 0, SERVICES_OUT, ""
            return 2, "", "boom"

        rc, _ = self._call(self.cli.reset, run)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
