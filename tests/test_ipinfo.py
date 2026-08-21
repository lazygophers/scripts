"""ipinfo 单元测试（mock subprocess.run / curl）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import ipinfo


def _fake_proc(stdout: str = "", returncode: int = 0):
    """构造 mock subprocess.CompletedProcess。"""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


class TestLanIp(unittest.TestCase):
    def test_ipconfig_hit(self):
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout="192.168.1.10\n")):
            self.assertEqual(ipinfo.lan_ip(), "192.168.1.10")

    def test_ipconfig_empty_fallback_to_socket(self):
        # en0/en1/en2 都空，hostname -I 也空，走 socket 兜底
        with patch("lib.ipinfo.run") as m_run:
            m_run.side_effect = [
                _fake_proc(""), _fake_proc(""), _fake_proc(""),
                _fake_proc(""),  # hostname -I
            ]
            # 走真实 socket 子模块的 socket.socket；只 mock IPAPI 探测点
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
            # 确认退出后原 socket 还原
            self.assertIs(_socket.socket, real_socket)


class TestWanInfo(unittest.TestCase):
    def test_ipinfo_success(self):
        # 显式传 source="ipinfo" 才走 ipinfo.io
        payload = json.dumps({
            "ip": "1.2.3.4", "city": "Tokyo", "region": "Tokyo", "country": "JP",
            "org": "AS1234 Test",
        })
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=payload)):
            info = ipinfo.wan_info(source="ipinfo")
            self.assertEqual(info["ip"], "1.2.3.4")
            self.assertEqual(info["country"], "JP")
            self.assertEqual(info["city"], "Tokyo")

    def test_default_source_is_ipapi(self):
        """默认 source 改为 ip-api：ip-api shape 且 status=fail 时返回 None。"""
        payload = json.dumps({"status": "fail", "message": "private range"})
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=payload)):
            self.assertIsNone(ipinfo.wan_info())

    def test_ipapi_success_normalized(self):
        payload = json.dumps({
            "status": "success", "query": "5.6.7.8", "country": "CN",
            "regionName": "Anhui", "city": "Hefei",
            "as": "AS9808 China Mobile", "org": "AS9808 China Mobile Group",
        })
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=payload)):
            info = ipinfo.wan_info(source="ip-api")
            self.assertEqual(info["ip"], "5.6.7.8")
            self.assertEqual(info["region"], "Anhui")  # 归一化 regionName → region
            self.assertEqual(info["city"], "Hefei")

    def test_ipapi_failure(self):
        payload = json.dumps({"status": "fail", "message": "private range"})
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout=payload)):
            self.assertIsNone(ipinfo.wan_info(source="ip-api"))

    def test_empty_stdout(self):
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout="")):
            self.assertIsNone(ipinfo.wan_info())

    def test_proxy_passed_to_curl(self):
        """指定 proxy 时 curl 应加 -x 参数。"""
        with patch("lib.ipinfo.run", return_value=_fake_proc(stdout='{"ip": "9.9.9.9"}')) as m_run:
            ipinfo.wan_info(proxy="http://127.0.0.1:7890")
            cmd = m_run.call_args[0][0]
            self.assertIn("-x", cmd)
            self.assertIn("http://127.0.0.1:7890", cmd)


class TestProxyInfo(unittest.TestCase):
    def test_no_proxy_env(self):
        env = {k: v for k, v in os.environ.items() if k not in (
            "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
        )}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(ipinfo.proxy_info())

    def test_https_proxy_env(self):
        # 默认走 ip-api：payload 必须是 ip-api shape
        payload = json.dumps({
            "status": "success", "query": "1.1.1.1", "country": "US",
            "regionName": "CA", "city": "LA", "org": "AS1234",
        })
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890"}), \
             patch("lib.ipinfo.run", return_value=_fake_proc(stdout=payload)):
            info = ipinfo.proxy_info()
            self.assertEqual(info["ip"], "1.1.1.1")


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
            # Wi-Fi 优先于 Ethernet
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
    """通过 ifconfig 里 bridge1..bridge99 有 IPv4 来识别热点（不依赖 SSID）。"""

    def _ifconfig(self, body: str):
        return _fake_proc(stdout=body)

    def test_bridge100_with_ipv4_is_hotspot(self):
        body = (
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 11:22:33:44:55:66\n"
            "bridge100: flags=8a63<UP,BROADCAST,SMART,RUNNING>\n"
            "\tether 12:9f:41:9d:56:64\n"
            "\tinet 172.20.0.0 netmask 0xffff0000 broadcast 172.20.255.255\n"
            "\tinet6 fe80::109f:41ff:fe9d:5664%bridge100 prefixlen 64\n"
        )
        with patch("lib.ipinfo.run", return_value=self._ifconfig(body)):
            self.assertTrue(ipinfo.is_hotspot_wifi())

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


class TestRender(unittest.TestCase):
    def test_plain(self):
        out = ipinfo.render([("内网 IP", "1.1.1.1"), ("网络类型", "Wi-Fi")])
        self.assertIn("内网 IP", out)
        self.assertIn("1.1.1.1", out)
        self.assertIn("Wi-Fi", out)

    def test_json(self):
        out = ipinfo.render([("k1", "v1"), ("k2", "v2")], json_mode=True)
        data = json.loads(out)
        self.assertEqual(data, {"k1": "v1", "k2": "v2"})


class TestCliImport(unittest.TestCase):
    """bin/ipinfo 反射注册表：确认所有子命令存在。"""

    def test_cli_subcommands(self):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()
        cli = mod.IpinfoCli()
        for name in ("all", "lan", "wan", "proxy", "net"):
            self.assertTrue(callable(getattr(cli, name)), f"missing subcommand: {name}")

    def test_call_runs_all_by_default(self):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()
        cli = mod.IpinfoCli()
        with patch.object(cli, "all") as m_all:
            cli()
            m_all.assert_called_once_with()

    def test_call_runs_listed_methods(self):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()
        cli = mod.IpinfoCli()
        with patch.object(cli, "lan") as m_lan, patch.object(cli, "wan") as m_wan:
            cli("lan", "wan")
            m_lan.assert_called_once_with()
            m_wan.assert_called_once_with()

    def test_type_alias_maps_to_net(self):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()
        cli = mod.IpinfoCli()
        with patch.object(cli, "net") as m_net:
            cli("type")
            m_net.assert_called_once_with()

    def test_unknown_returns_none(self):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("ipinfo_test_mod", str(Path(__file__).resolve().parent.parent / "bin" / "ipinfo")).load_module()
        cli = mod.IpinfoCli()
        self.assertIsNone(cli("nope"))


if __name__ == "__main__":
    unittest.main()
