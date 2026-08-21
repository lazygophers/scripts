"""ipinfo 单元测试（mock subprocess.run）。"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import ipinfo


def _fake_proc(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


class TestLanIp(unittest.TestCase):
    def test_ipconfig_hit(self):
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout="192.168.1.10\n")):
            self.assertEqual(ipinfo.lan_ip(), "192.168.1.10")

    def test_ipconfig_empty_fallback_to_socket(self):
        with patch("lib.ipinfo.run") as m_run:
            m_run.side_effect = [_fake_proc(""), _fake_proc(""), _fake_proc(""), _fake_proc("")]
            import socket as _socket
            real_socket = _socket.socket

            class FakeSock:
                def __init__(self, *a, **kw):
                    pass

                def connect(self, *a):
                    pass

                def getsockname(self):
                    return ("10.0.0.5", 0)

                def close(self):
                    pass

            with patch.object(_socket, "socket", FakeSock):
                self.assertEqual(ipinfo.lan_ip(), "10.0.0.5")
            self.assertIs(_socket.socket, real_socket)


class TestNetType(unittest.TestCase):
    def test_wifi_detected(self):
        hwports = (
            "Hardware Port: Ethernet Adapter (en3)\n"
            "Device: en3\n"
            "Ethernet Address: aa:bb:cc:dd:ee:f0\n"
            "\n"
            "Hardware Port: Wi-Fi\n"
            "Device: en0\n"
            "Ethernet Address: 11:22:33:44:55:66\n"
        )
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=hwports)), \
             patch.object(ipinfo, "_ip_of", side_effect=lambda dev: "192.168.1.5" if dev == "en0" else None):
            self.assertEqual(ipinfo.net_type(), ["Wi-Fi"])

    def test_ethernet_detected(self):
        hwports = (
            "Hardware Port: Ethernet Adapter (en3)\n"
            "Device: en3\n"
            "Ethernet Address: aa:bb:cc:dd:ee:f0\n"
        )
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=hwports)), \
             patch.object(ipinfo, "_ip_of", side_effect=lambda dev: "10.0.0.1" if dev == "en3" else None):
            self.assertEqual(ipinfo.net_type(), ["Ethernet"])

    def test_wifi_and_ethernet_both_shown(self):
        hwports = (
            "Hardware Port: Ethernet Adapter (en3)\n"
            "Device: en3\n"
            "\n"
            "Hardware Port: Wi-Fi\n"
            "Device: en0\n"
        )
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=hwports)), \
             patch.object(ipinfo, "_ip_of", side_effect=lambda dev: "1.2.3.4" if dev in ("en0", "en3") else None):
            self.assertEqual(ipinfo.net_type(), ["Wi-Fi", "Ethernet"])

    def test_no_active_interface(self):
        hwports = "Hardware Port: Thunderbolt Bridge\nDevice: bridge0\n"
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=hwports)), \
             patch.object(ipinfo, "_ip_of", return_value=None):
            self.assertEqual(ipinfo.net_type(), ["None"])

    def test_empty_networksetup_output(self):
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout="")):
            self.assertEqual(ipinfo.net_type(), ["Unknown"])


class TestIsHotspotWifi(unittest.TestCase):
    """通过 ifconfig 里 bridge1..bridge99 有 IPv4 + netstat default 路由判断。"""

    def _ifconfig(self, body: str):
        return _fake_proc(stdout=body)

    def _netstat(self, body: str):
        return _fake_proc(stdout=body)

    def test_bridge100_with_ipv4_is_hotspot(self):
        ifc = (
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 11:22:33:44:55:66\n"
            "bridge100: flags=8a63<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 12:9f:41:9d:56:64\n"
            "\tinet 172.20.0.0 netmask 0xffff0000 broadcast 172.20.255.255\n"
            "\tinet6 fe80::109f:41ff:fe9d:5664%bridge100 prefixlen 64\n"
        )
        ns = (
            "Routing tables\n"
            "Internet:\n"
            "Destination        Gateway            Flags        Netif Expire\n"
            "default            172.20.0.1         UGScg          bridge100\n"
        )
        with patch("lib.ipinfo.run", side_effect=[self._ifconfig(ifc), self._netstat(ns)]):
            self.assertTrue(ipinfo.is_hotspot_wifi())

    def test_bridge_with_ipv4_but_rejected_route_is_not_hotspot(self):
        """bridge100 有 IPv4 但 default 路由被 ! reject 标记 → 不算热点。"""
        ifc = (
            "bridge100: flags=8a63<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 12:9f:41:9d:56:64\n"
            "\tinet 172.20.0.0 netmask 0xffff0000 broadcast 172.20.255.255\n"
        )
        ns = (
            "Routing tables\n"
            "Internet:\n"
            "Destination        Gateway            Flags        Netif Expire\n"
            "default            192.168.1.1        UGScg            en5\n"
            "default            172.20.0.1         UGScg !        bridge100\n"
        )
        with patch("lib.ipinfo.run", side_effect=[self._ifconfig(ifc), self._netstat(ns)]):
            self.assertFalse(ipinfo.is_hotspot_wifi())

    def test_bridge0_only_is_not_hotspot(self):
        body = (
            "bridge0: flags=8863<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 36:a7:a4:a1:47:40\n"
        )
        with patch("lib.ipinfo.run", return_value=self._ifconfig(body)):
            self.assertFalse(ipinfo.is_hotspot_wifi())

    def test_no_bridge_is_not_hotspot(self):
        body = (
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 11:22:33:44:55:66\n"
            "\tinet 192.168.1.5 netmask 0xffffff00\n"
        )
        with patch("lib.ipinfo.run", return_value=self._ifconfig(body)):
            self.assertFalse(ipinfo.is_hotspot_wifi())

    def test_bridge_without_ipv4_is_not_hotspot(self):
        body = (
            "bridge100: flags=8a63<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 12:9f:41:9d:56:64\n"
        )
        with patch("lib.ipinfo.run", return_value=self._ifconfig(body)):
            self.assertFalse(ipinfo.is_hotspot_wifi())


class TestCliImport(unittest.TestCase):
    """bin/ipinfo 反射注册表：确认子命令存在且 wan/proxy 已移除。"""

    def _load(self):
        from importlib.machinery import SourceFileLoader
        return SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()

    def test_cli_subcommands(self):
        cli = self._load().IpinfoCli()
        for name in ("all", "lan", "net"):
            self.assertTrue(callable(getattr(cli, name)), f"missing subcommand: {name}")
        for removed in ("wan", "proxy"):
            self.assertFalse(hasattr(cli, removed), f"subcommand should be removed: {removed}")

    def test_call_runs_all_by_default(self):
        cli = self._load().IpinfoCli()
        with patch.object(cli, "all") as m_all:
            cli()
            m_all.assert_called_once_with()

    def test_call_runs_listed_methods(self):
        cli = self._load().IpinfoCli()
        with patch.object(cli, "lan") as m_lan, patch.object(cli, "net") as m_net:
            cli("lan", "net")
            m_lan.assert_called_once_with()
            m_net.assert_called_once_with()

    def test_type_alias_maps_to_net(self):
        cli = self._load().IpinfoCli()
        with patch.object(cli, "net") as m_net:
            cli("type")
            m_net.assert_called_once_with()

    def test_unknown_returns_none(self):
        cli = self._load().IpinfoCli()
        self.assertIsNone(cli("nope"))


if __name__ == "__main__":
    unittest.main()