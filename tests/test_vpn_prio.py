"""vpn-prio 单元测试（mock pgrep + networksetup + netstat）。"""
from __future__ import annotations

import importlib.machinery
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_module():
    """从 bin/vpn-prio 加载模块（路径含连字符，普通 import 不行）。"""
    return importlib.machinery.SourceFileLoader(
        "vp_under_test",
        str(Path(__file__).resolve().parent.parent / "bin" / "vpn-prio"),
    ).load_module()


vp = _load_module()
_bridge100_default_state = vp._bridge100_default_state
_desired_order = vp._desired_order
_detect_openvpn = vp._detect_openvpn
_list_services = vp._list_services


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestListServices(unittest.TestCase):
    def test_parses_real_format(self):
        body = (
            "An asterisk (*) denotes that a network service is disabled.\n"
            "(1) USB 10/100/1000 LAN\n"
            "(Hardware Port: USB 10/100/1000 LAN, Device: en5)\n"
            "\n"
            "(2) USB 10/100/1000 LAN 2\n"
            "(Hardware Port: USB 10/100/1000 LAN, Device: en6)\n"
            "\n"
            "(3) Wi-Fi\n"
            "(Hardware Port: Wi-Fi, Device: en0)\n"
            "\n"
            "(4) Tailscale 2\n"
            "(Hardware Port: io.tailscale.ipn.macsys, Device: )\n"
        )
        with patch.object(vp, "_run", return_value=(0, body, "")):
            self.assertEqual(
                _list_services(),
                ["USB 10/100/1000 LAN", "USB 10/100/1000 LAN 2", "Wi-Fi", "Tailscale 2"],
            )

    def test_skips_disabled(self):
        body = "(1) Wi-Fi *\n(Hardware Port: Wi-Fi, Device: en0)\n"
        with patch.object(vp, "_run", return_value=(0, body, "")):
            self.assertEqual(_list_services(), ["Wi-Fi"])

    def test_empty(self):
        with patch.object(vp, "_run", return_value=(0, "", "")):
            self.assertEqual(_list_services(), [])


class TestDesiredOrder(unittest.TestCase):
    def test_usb_first_then_wifi_then_others(self):
        services = ["Tailscale 2", "Wi-Fi", "USB 10/100/1000 LAN", "USB 10/100/1000 LAN 2"]
        self.assertEqual(
            _desired_order(services),
            ["USB 10/100/1000 LAN", "USB 10/100/1000 LAN 2", "Wi-Fi", "Tailscale 2"],
        )

    def test_no_change_returns_same(self):
        services = ["USB 10/100/1000 LAN", "Wi-Fi", "Tailscale 2"]
        self.assertEqual(_desired_order(services), services)


class TestDetectOpenVpn(unittest.TestCase):
    def test_running(self):
        body = "577 /Library/Frameworks/OpenVPNConnect.framework/Versions/Current/usr/sbin/ovpnagent\n"
        with patch.object(vp, "_run", return_value=(0, body, "")):
            r = _detect_openvpn()
            self.assertTrue(r["running"])
            self.assertEqual(r["pids"], ["577"])
            self.assertIn("ovpnagent", r["matched_pattern"])

    def test_not_running(self):
        with patch.object(vp, "_run", return_value=(1, "", "")):
            r = _detect_openvpn()
            self.assertFalse(r["running"])
            self.assertEqual(r["pids"], [])


class TestBridge100DefaultState(unittest.TestCase):
    def test_rejected(self):
        body = (
            "Routing tables\n"
            "default            192.168.1.1        UGScg                 en5\n"
            "default            link#23            UCSIg           bridge100      !\n"
        )
        with patch.object(vp, "_run", return_value=(0, body, "")):
            self.assertEqual(_bridge100_default_state(), "rejected")

    def test_active(self):
        body = "default            192.168.1.1        UGScg           bridge100\n"
        with patch.object(vp, "_run", return_value=(0, body, "")):
            self.assertEqual(_bridge100_default_state(), "active")

    def test_absent(self):
        with patch.object(vp, "_run", return_value=(0, "Routing tables\ndefault 192.168.1.1 en5\n", "")):
            self.assertEqual(_bridge100_default_state(), "absent")


class TestCliImport(unittest.TestCase):
    def _load(self):
        return _load_module()

    def test_subcommands_present(self):
        cli = self._load().VpnPrioCli()
        for name in ("status", "apply", "reset"):
            self.assertTrue(callable(getattr(cli, name)), f"missing: {name}")

    def test_bare_call_runs_status(self):
        cli = self._load().VpnPrioCli()
        with patch.object(cli, "status", return_value=0) as m_status:
            cli()
            m_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()