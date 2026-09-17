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
    """不是 root 时自己去拿：用 sudo 原样重跑一遍，而不是丢一句「需 sudo 运行」。

    一台机器上十几个网络服务要逐个改，中途才第一次 sudo 就可能撞上授权过期；
    先把整个进程提成 root，后面每一条 networksetup 都不必再问。
    """

    def test_disable_without_root_reruns_itself_and_touches_nothing(self) -> None:
        with mock.patch.object(iv.privilege, "become_root") as become, \
             mock.patch.object(iv.subprocess, "run") as run:
            self._call(self.cli.disable)
        become.assert_called_once()
        # 重跑的是自己这个脚本，参数原样带过去
        self.assertEqual(become.call_args.args[0], iv.SCRIPT_PATH)
        del run

    def test_when_sudo_is_unavailable_it_says_so_instead_of_half_doing_it(self) -> None:
        with mock.patch.object(iv.privilege, "become_root",
                               side_effect=iv.privilege.NeedRoot("起不了 sudo")), \
             mock.patch.object(iv.subprocess, "run") as run:
            rc, _ = self._call(self.cli.enable)
        self.assertEqual(rc, 13)
        run.assert_not_called()
        self.cli._r.err.assert_called_once()


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

        # 已经是 root 时提权什么都不做，等价于把它桩成空动作
        with mock.patch.object(iv.privilege, "become_root"), \
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
