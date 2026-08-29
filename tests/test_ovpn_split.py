"""lib/ovpn_split.py 的单元测试：域名规则、DNS 报文解析、resolver 文件、路由命令。"""
from __future__ import annotations

import ipaddress
import pathlib
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import ovpn_split as S


def _dns_query(name: str, qtype: int = 1) -> bytes:
    """拼一个最小的 DNS 查询报文。"""
    header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return header + qname + struct.pack(">HH", qtype, 1)


def _dns_answer(name: str, records: list[tuple[int, bytes]]) -> bytes:
    """拼一个 DNS 响应：问题段 + 若干条 answer（用压缩指针指回问题段）。"""
    header = struct.pack(">HHHHHH", 0x1234, 0x8180, 1, len(records), 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    body = qname + struct.pack(">HH", 1, 1)
    for rtype, rdata in records:
        body += b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata
    return header + body


class TestNormalizeDomain(unittest.TestCase):
    def test_forms(self):
        for raw in ("*.example.com", ".example.com", "example.com",
                    "  *.Example.COM  ", "example.com."):
            self.assertEqual(S.normalize_domain(raw), "example.com", raw)

    def test_empty(self):
        self.assertEqual(S.normalize_domain(""), "")
        self.assertEqual(S.normalize_domain("   "), "")


class TestDomainMatches(unittest.TestCase):
    def test_exact_and_subdomain(self):
        self.assertTrue(S.domain_matches("example.com", "example.com"))
        self.assertTrue(S.domain_matches("api.example.com", "example.com"))
        self.assertTrue(S.domain_matches("a.b.example.com.", "example.com"))
        self.assertTrue(S.domain_matches("API.Example.com", "example.com"))

    def test_no_false_positive(self):
        # notexample.com 不是 example.com 的子域
        self.assertFalse(S.domain_matches("notexample.com", "example.com"))
        self.assertFalse(S.domain_matches("example.com.evil.net", "example.com"))
        self.assertFalse(S.domain_matches("example.com", ""))


class TestParseQuestionName(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(S.parse_question_name(_dns_query("api.example.com")),
                         "api.example.com")

    def test_too_short(self):
        self.assertIsNone(S.parse_question_name(b"\x00" * 5))

    def test_compression_pointer_rejected(self):
        msg = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0) + b"\xc0\x0c"
        self.assertIsNone(S.parse_question_name(msg))

    def test_truncated_label(self):
        msg = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0) + b"\x0aab"
        self.assertIsNone(S.parse_question_name(msg))


class TestParseAnswerIps(unittest.TestCase):
    def test_a_records(self):
        msg = _dns_answer("example.com", [
            (1, ipaddress.IPv4Address("203.0.113.7").packed),
            (1, ipaddress.IPv4Address("203.0.113.8").packed),
        ])
        self.assertEqual(S.parse_answer_ips(msg), ["203.0.113.7", "203.0.113.8"])

    def test_aaaa_record(self):
        msg = _dns_answer("example.com", [(28, ipaddress.IPv6Address("2001:db8::1").packed)])
        self.assertEqual(S.parse_answer_ips(msg), ["2001:db8::1"])

    def test_cname_skipped(self):
        msg = _dns_answer("example.com", [
            (5, b"\x03cdn\xc0\x0c"),  # CNAME 不是 IP，跳过
            (1, ipaddress.IPv4Address("198.51.100.9").packed),
        ])
        self.assertEqual(S.parse_answer_ips(msg), ["198.51.100.9"])

    def test_no_answer(self):
        self.assertEqual(S.parse_answer_ips(_dns_query("example.com")), [])
        self.assertEqual(S.parse_answer_ips(b""), [])


class TestSystemNameservers(unittest.TestCase):
    def test_parses_and_drops_loopback(self):
        text = "# comment\nnameserver 127.0.0.1\nnameserver 192.168.1.1\nnameserver 1.1.1.1\n"
        with mock.patch.object(pathlib.Path, "read_text", return_value=text):
            self.assertEqual(S.system_nameservers(), ["192.168.1.1", "1.1.1.1"])

    def test_fallback_when_all_loopback(self):
        with mock.patch.object(pathlib.Path, "read_text", return_value="nameserver 127.0.0.1\n"):
            self.assertEqual(S.system_nameservers(), ["1.1.1.1", "8.8.8.8"])

    def test_fallback_when_unreadable(self):
        with mock.patch.object(pathlib.Path, "read_text", side_effect=OSError):
            self.assertEqual(S.system_nameservers(), ["1.1.1.1", "8.8.8.8"])


class TestResolverFiles(unittest.TestCase):
    def test_content_has_marker_and_port(self):
        c = S.resolver_file_content(5354)
        self.assertTrue(c.startswith(S.RESOLVER_MARKER))
        self.assertIn("nameserver 127.0.0.1", c)
        self.assertIn("port 5354", c)

    def test_is_ours(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            ours = base / "example.com"
            ours.write_text(S.resolver_file_content(5354))
            theirs = base / "corp.internal"
            theirs.write_text("nameserver 10.0.0.1\n")
            self.assertTrue(S.is_ours(ours))
            self.assertFalse(S.is_ours(theirs))
            self.assertFalse(S.is_ours(base / "missing"))

            self.assertEqual(S.stale_resolver_files(base), [ours])

    def test_stale_on_missing_dir(self):
        self.assertEqual(S.stale_resolver_files(pathlib.Path("/nonexistent-xyz")), [])

    def test_clean_runs_sudo_rm(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            (base / "example.com").write_text(S.resolver_file_content(5354))
            (base / "keep.me").write_text("nameserver 10.0.0.1\n")
            with mock.patch.object(S.subprocess, "run") as run:
                n = S.clean_resolver_files(None, base)
            self.assertEqual(n, 1)
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[:3], ["sudo", "rm", "-f"])
            self.assertIn(str(base / "example.com"), cmd)
            self.assertNotIn(str(base / "keep.me"), cmd)

    def test_clean_noop_when_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(S.subprocess, "run") as run:
                self.assertEqual(S.clean_resolver_files(None, pathlib.Path(d)), 0)
            run.assert_not_called()

    def test_write_uses_sudo_tee(self):
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            S.write_resolver_files(["a.com", "b.com"], 5354, None, pathlib.Path("/etc/resolver"))
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0][:3], ["sudo", "mkdir", "-p"])
        self.assertEqual(cmds[1], ["sudo", "tee", "/etc/resolver/a.com"])
        self.assertEqual(cmds[2], ["sudo", "tee", "/etc/resolver/b.com"])

    def test_write_noop_without_domains(self):
        with mock.patch.object(S.subprocess, "run") as run:
            S.write_resolver_files([], 5354, None)
        run.assert_not_called()


class TestRouteTable(unittest.TestCase):
    def test_add_host_command(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertTrue(t.add_host("203.0.113.7"))
        self.assertEqual(run.call_args[0][0],
                         ["sudo", "route", "-n", "add", "-inet", "-host",
                          "203.0.113.7", "-interface", "utun4"])

    def test_add_host_ipv6_family(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            t.add_host("2001:db8::1")
        self.assertIn("-inet6", run.call_args[0][0])

    def test_add_host_dedup(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertTrue(t.add_host("203.0.113.7"))
            self.assertFalse(t.add_host("203.0.113.7"))  # 第二次直接跳过
        self.assertEqual(run.call_count, 1)

    def test_file_exists_is_success(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stderr="route: writing to routing socket: File exists")
            self.assertTrue(t.add_host("203.0.113.7"))
        self.assertIn("203.0.113.7", t.added)

    def test_real_failure_forgets_ip(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stderr="Network is unreachable")
            self.assertFalse(t.add_host("203.0.113.7"))
        # 没加成功就不能记账，否则 flush 会去删一条不存在的路由
        self.assertNotIn("203.0.113.7", t.added)

    def test_add_network(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertTrue(t.add_network("10.8.0.0/16"))
        self.assertEqual(run.call_args[0][0],
                         ["sudo", "route", "-n", "add", "-inet", "-net",
                          "10.8.0.0/16", "-interface", "utun4"])

    def test_add_network_invalid(self):
        t = S.RouteTable("utun4")
        with mock.patch.object(S.subprocess, "run") as run:
            self.assertFalse(t.add_network("not-a-cidr"))
        run.assert_not_called()

    def test_flush_deletes_all(self):
        t = S.RouteTable("utun4")
        t.added = {"203.0.113.7", "10.8.0.0/16"}
        with mock.patch.object(S.subprocess, "run") as run:
            t.flush()
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertIn(["sudo", "route", "-n", "delete", "-inet", "-net", "10.8.0.0/16"], cmds)
        self.assertIn(["sudo", "route", "-n", "delete", "-inet", "-host", "203.0.113.7"], cmds)
        self.assertEqual(t.added, set())


class TestTunForIp(unittest.TestCase):
    def test_lookup(self):
        with mock.patch("lib.ovpn.tun_interfaces",
                        return_value=[("utun3", "10.0.0.1"), ("utun4", "10.8.0.6")]):
            self.assertEqual(S.tun_for_ip("10.8.0.6"), "utun4")
            self.assertIsNone(S.tun_for_ip("10.8.0.99"))


class TestDnsProxyHandle(unittest.TestCase):
    def test_matching_domain_triggers_callback(self):
        got = []
        p = S.DnsProxy(5354, ["1.1.1.1"], ["example.com"],
                       lambda q, ips: got.append((q, ips)))
        p.sock = mock.Mock()
        answer = _dns_answer("api.example.com",
                             [(1, ipaddress.IPv4Address("203.0.113.7").packed)])
        with mock.patch.object(p, "forward", return_value=answer):
            p._handle(_dns_query("api.example.com"), ("127.0.0.1", 1234))
        self.assertEqual(got, [("api.example.com", ["203.0.113.7"])])
        p.sock.sendto.assert_called_once_with(answer, ("127.0.0.1", 1234))

    def test_non_matching_domain_still_forwards(self):
        got = []
        p = S.DnsProxy(5354, ["1.1.1.1"], ["example.com"],
                       lambda q, ips: got.append((q, ips)))
        p.sock = mock.Mock()
        answer = _dns_answer("other.net", [(1, ipaddress.IPv4Address("198.51.100.1").packed)])
        with mock.patch.object(p, "forward", return_value=answer):
            p._handle(_dns_query("other.net"), ("127.0.0.1", 1234))
        self.assertEqual(got, [])
        p.sock.sendto.assert_called_once()

    def test_question_label_has_name_and_type(self):
        self.assertEqual(S.question_label(_dns_query("api.example.com")),
                         "api.example.com (A)")
        self.assertEqual(S.question_label(b"\x00" * 4), "未知查询")

    def test_all_upstreams_dead_warns_with_query_name(self):
        r = _FakeReporter()
        p = S.DnsProxy(5354, ["192.0.2.1"], ["example.com"], lambda q, ips: None,
                       r, timeout=0.01)
        with mock.patch.object(S.socket, "socket", side_effect=OSError("no route")):
            self.assertIsNone(p.forward(_dns_query("api.example.com")))
        warns = [m for k, m in r.lines if k == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("api.example.com (A)", warns[0])
        self.assertIn("192.0.2.1", warns[0])

    def test_upstream_dead_returns_nothing(self):
        p = S.DnsProxy(5354, ["1.1.1.1"], ["example.com"], lambda q, ips: None)
        p.sock = mock.Mock()
        with mock.patch.object(p, "forward", return_value=None):
            p._handle(_dns_query("api.example.com"), ("127.0.0.1", 1234))
        p.sock.sendto.assert_not_called()


class _FakeReporter:
    def __init__(self):
        self.lines = []

    def _add(self, kind, msg):
        self.lines.append((kind, msg))

    def ok(self, m):
        self._add("ok", m)

    def err(self, m):
        self._add("err", m)

    def warn(self, m):
        self._add("warn", m)

    def info(self, m):
        self._add("info", m)

    def step(self, m):
        self._add("step", m)


class TestSplitTunnel(unittest.TestCase):
    def test_disabled_without_rules(self):
        st = S.SplitTunnel({}, _FakeReporter())
        self.assertFalse(st.enabled)

    def test_enabled_and_normalized(self):
        st = S.SplitTunnel({"routes": {"domains": ["*.a.com", "a.com", "B.COM"],
                                       "cidrs": ["10.8.0.0/16"]}}, _FakeReporter())
        self.assertTrue(st.enabled)
        self.assertEqual(st.domains, ["a.com", "b.com"])  # 去重 + 归一
        self.assertEqual(st.cidrs, ["10.8.0.0/16"])

    def test_cidr_only_is_enabled(self):
        st = S.SplitTunnel({"routes": {"cidrs": ["10.8.0.0/16"]}}, _FakeReporter())
        self.assertTrue(st.enabled)

    def test_port_and_upstream_defaults(self):
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, _FakeReporter())
        self.assertEqual(st.port, S.DEFAULT_DNS_PORT)
        self.assertTrue(st.upstreams)

    def test_custom_port_and_upstream(self):
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]},
                            "dns_port": 15353, "dns_upstream": ["9.9.9.9"]}, _FakeReporter())
        self.assertEqual(st.port, 15353)
        self.assertEqual(st.upstreams, ["9.9.9.9"])

    def test_start_without_tun_reports_error(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, r)
        with mock.patch.object(S, "tun_for_ip", return_value=None):
            st.start("10.8.0.6")
        self.assertIsNone(st.table)
        self.assertTrue(any(k == "err" for k, _ in r.lines))

    def test_start_adds_cidrs_and_writes_resolver(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"domains": ["a.com"], "cidrs": ["10.8.0.0/16"]}}, r)
        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.RouteTable, "add_network", return_value=True) as add_net, \
             mock.patch.object(S.DnsProxy, "start") as proxy_start, \
             mock.patch.object(S, "write_resolver_files") as write:
            st.start("10.8.0.6")
        add_net.assert_called_once_with("10.8.0.0/16")
        proxy_start.assert_called_once()
        write.assert_called_once_with(["a.com"], S.DEFAULT_DNS_PORT, r)

    def test_start_cidr_only_skips_proxy(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"cidrs": ["10.8.0.0/16"]}}, r)
        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.RouteTable, "add_network", return_value=True), \
             mock.patch.object(S.DnsProxy, "start") as proxy_start:
            st.start("10.8.0.6")
        proxy_start.assert_not_called()
        self.assertIsNone(st.proxy)

    def test_start_proxy_bind_failure_is_reported(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, r)
        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.DnsProxy, "start", side_effect=OSError("Address already in use")), \
             mock.patch.object(S, "write_resolver_files") as write:
            st.start("10.8.0.6")
        self.assertIsNone(st.proxy)
        # 代理没起来就不能写 resolver 文件，否则该域名直接解析失败
        write.assert_not_called()
        self.assertTrue(any(k == "err" for k, _ in r.lines))

    def test_stop_order_and_cleanup(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, r)
        st.table = mock.Mock()
        st.proxy = mock.Mock()
        calls = []
        st.proxy.stop.side_effect = lambda: calls.append("proxy")
        st.table.flush.side_effect = lambda: calls.append("routes")
        with mock.patch.object(S, "clean_resolver_files",
                               side_effect=lambda *a, **k: calls.append("resolver")):
            st.stop()
        # 先撤 DNS 指向再停代理，避免中间窗口里查询打到已关闭的端口
        self.assertEqual(calls, ["resolver", "proxy", "routes"])
        self.assertIsNone(st.proxy)
        self.assertIsNone(st.table)

    def test_pushed_dns_beats_system_but_not_config(self):
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, _FakeReporter())
        st.note_pushed_dns(["10.8.0.1", "10.8.0.1", "10.8.0.2"])
        self.assertEqual(st.upstreams, ["10.8.0.1", "10.8.0.2"])  # 去重，压过系统 DNS
        fixed = S.SplitTunnel({"routes": {"domains": ["a.com"]},
                               "dns_upstream": ["9.9.9.9"]}, _FakeReporter())
        fixed.note_pushed_dns(["10.8.0.1"])
        self.assertEqual(fixed.upstreams, ["9.9.9.9"])  # 配置写死的优先

    def test_start_routes_pushed_dns_through_tun(self):
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, _FakeReporter())
        st.note_pushed_dns(["10.8.0.1"])
        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.RouteTable, "add_host", return_value=True) as add_host, \
             mock.patch.object(S.DnsProxy, "start"), \
             mock.patch.object(S, "write_resolver_files"):
            st.start("10.8.0.6")
        add_host.assert_called_once_with("10.8.0.1")

    def test_proxy_reads_upstreams_lazily(self):
        st = S.SplitTunnel({"routes": {"domains": ["a.com"]}}, _FakeReporter())
        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.DnsProxy, "start"), \
             mock.patch.object(S, "write_resolver_files"):
            st.start("10.8.0.6")
        # PUSH_REPLY 可能晚于代理启动到达，代理必须每次查询时重新取上游
        st.note_pushed_dns(["10.8.0.1"])
        self.assertEqual(st.proxy.upstreams, ["10.8.0.1"])

    def test_stop_is_idempotent(self):
        st = S.SplitTunnel({}, _FakeReporter())
        with mock.patch.object(S, "clean_resolver_files"):
            st.stop()
            st.stop()


if __name__ == "__main__":
    unittest.main()
