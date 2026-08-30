"""lib/ovpn_split.py 的单元测试：域名规则、DNS 报文解析、resolver 文件、路由命令。"""
from __future__ import annotations

import ipaddress
import pathlib
import socket
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


class TestMalformedDnsMessages(unittest.TestCase):
    def test_question_name_unterminated(self):
        header = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0)
        # 长度前缀声明 3 字节但报文到此为止，且没有结尾 0
        self.assertIsNone(S.parse_question_name(header + b"\x03ab"))

    def test_question_name_runs_out_of_bytes(self):
        header = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0)
        # label 恰好读完就没有结尾 0 字节 → while 退出后返回 None
        self.assertIsNone(S.parse_question_name(header + b"\x02ab"))

    def test_question_label_without_qtype(self):
        header = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0)
        msg = header + b"\x01a\x00"  # 有域名但缺 QTYPE
        self.assertEqual(S.question_label(msg), "a")

    def test_skip_name_unterminated_returns_end(self):
        self.assertEqual(S._skip_name(b"\x03abc", 0), 4)

    def test_answer_truncated_record_stops(self):
        answer = _dns_answer("a.com", [(1, bytes([1, 2, 3, 4]))])[:-8]
        self.assertEqual(S.parse_answer_ips(answer), [])


class TestSystemNameservers(unittest.TestCase):
    def test_skips_invalid_and_loopback(self):
        text = "\n".join([
            "# comment",
            "nameserver not-an-ip",
            "nameserver 127.0.0.1",
            "nameserver 9.9.9.9",
        ])
        with mock.patch.object(pathlib.Path, "read_text", return_value=text):
            self.assertEqual(S.system_nameservers(), ["9.9.9.9"])

    def test_falls_back_when_unreadable(self):
        with mock.patch.object(pathlib.Path, "read_text", side_effect=OSError):
            servers = S.system_nameservers()
        self.assertEqual(servers, S.FALLBACK_NAMESERVERS)

    def test_fallback_covers_both_regions(self):
        # 一份配置两地都能用：国内国外各至少一台，靠 forward() 并行赛跑挑通的那台
        self.assertIn("223.5.5.5", S.FALLBACK_NAMESERVERS)
        self.assertIn("1.1.1.1", S.FALLBACK_NAMESERVERS)

    def test_fallback_list_is_copied_per_call(self):
        with mock.patch.object(pathlib.Path, "read_text", side_effect=OSError):
            first = S.system_nameservers()
            first.append("9.9.9.9")
            second = S.system_nameservers()
        self.assertNotIn("9.9.9.9", second)
        self.assertNotIn("9.9.9.9", S.FALLBACK_NAMESERVERS)


class _Run:
    """subprocess.run 替身，按调用顺序返回预设结果。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        rc, stderr = self.results.pop(0) if self.results else (0, "")
        return mock.Mock(returncode=rc, stderr=stderr, stdout="")


class TestRouteTableReporting(unittest.TestCase):
    def test_add_host_reports_success(self):
        r = _FakeReporter()
        t = S.RouteTable("utun4", r)
        with mock.patch.object(S.subprocess, "run", _Run([(0, "")])):
            self.assertTrue(t.add_host("1.2.3.4"))
        self.assertIn(("step", "路由 1.2.3.4 → utun4"), r.lines)

    def test_add_host_warns_and_forgets_on_failure(self):
        r = _FakeReporter()
        t = S.RouteTable("utun4", r)
        with mock.patch.object(S.subprocess, "run", _Run([(1, "network is down")])):
            self.assertFalse(t.add_host("1.2.3.4"))
        self.assertEqual([k for k, _ in r.lines], ["warn"])
        self.assertNotIn("1.2.3.4", t.added)

    def test_add_network_invalid_cidr_warns(self):
        r = _FakeReporter()
        t = S.RouteTable("utun4", r)
        self.assertFalse(t.add_network("not-a-cidr"))
        self.assertIn("不是合法网段", r.lines[0][1])

    def test_add_network_success_reports(self):
        r = _FakeReporter()
        t = S.RouteTable("utun4", r)
        with mock.patch.object(S.subprocess, "run", _Run([(0, "")])):
            self.assertTrue(t.add_network("10.8.0.0/16"))
        self.assertIn("10.8.0.0/16", t.added)
        self.assertIn(("step", "路由 10.8.0.0/16 → utun4"), r.lines)

    def test_add_network_failure_warns(self):
        r = _FakeReporter()
        t = S.RouteTable("utun4", r)
        with mock.patch.object(S.subprocess, "run", _Run([(1, "boom")])):
            self.assertFalse(t.add_network("10.8.0.0/16"))
        self.assertIn("加网段失败", r.lines[0][1])

    def test_add_network_ipv6_family(self):
        t = S.RouteTable("utun4")
        runner = _Run([(0, "")])
        with mock.patch.object(S.subprocess, "run", runner):
            self.assertTrue(t.add_network("fd00::/64"))
        self.assertIn("-inet6", runner.calls[0])


class TestResolverFileReporting(unittest.TestCase):
    def test_clean_reports_names(self):
        r = _FakeReporter()
        files = [pathlib.Path("/etc/resolver/a.com")]
        with mock.patch.object(S, "stale_resolver_files", return_value=files), \
             mock.patch.object(S.subprocess, "run", _Run([(0, "")])):
            self.assertEqual(S.clean_resolver_files(r), 1)
        self.assertIn("a.com", r.lines[0][1])

    def test_write_warns_on_failure_and_reports_success(self):
        r = _FakeReporter()
        with mock.patch.object(S.subprocess, "run", _Run([(0, ""), (1, "denied")])):
            S.write_resolver_files(["a.com"], 5354, r, pathlib.Path("/tmp/resolver-test"))
        kinds = [k for k, _ in r.lines]
        self.assertEqual(kinds, ["warn", "step"])
        self.assertIn("denied", r.lines[0][1])
        self.assertIn("127.0.0.1:5354", r.lines[1][1])


class _FakeSock:
    """UDP socket 替身：记录发出去的查询，按需模拟发送/读取失败。"""

    def __init__(self, answer: bytes | None = None, *,
                 send_error: bool = False, recv_error: bool = False):
        self.answer = answer
        self.send_error = send_error
        self.recv_error = recv_error
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def setblocking(self, flag):
        pass

    def sendto(self, data, addr):
        if self.send_error:
            raise OSError("network is unreachable")
        self.sent.append((data, addr))

    def recvfrom(self, size):
        if self.recv_error or self.answer is None:
            raise OSError("connection refused")
        return self.answer, ("127.0.0.1", 53)

    def close(self):
        self.closed = True


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestDnsProxyLifecycle(unittest.TestCase):
    def test_serve_forwards_and_answers_client(self):
        port = _free_port()
        seen: list[tuple[str, list[str]]] = []
        answer = _dns_answer("api.example.com", [(1, bytes([93, 184, 216, 34]))])
        proxy = S.DnsProxy(port, ["192.0.2.1"], ["example.com"],
                           lambda q, ips: seen.append((q, ips)))
        with mock.patch.object(S.DnsProxy, "forward", return_value=answer):
            proxy.start()
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                client.settimeout(3)
                client.sendto(_dns_query("api.example.com"), ("127.0.0.1", port))
                data, _ = client.recvfrom(4096)
                client.close()
            finally:
                proxy.stop()
        self.assertEqual(data, answer)
        self.assertEqual(seen, [("api.example.com", ["93.184.216.34"])])
        self.assertIsNone(proxy.sock)

    def test_stop_without_start_is_safe(self):
        S.DnsProxy(_free_port(), [], [], lambda q, ips: None).stop()

    def test_serve_exits_on_socket_error(self):
        proxy = S.DnsProxy(5354, [], [], lambda q, ips: None)
        proxy.sock = mock.Mock()
        proxy.sock.recvfrom.side_effect = OSError("closed")
        proxy._serve()  # 不应抛出，循环直接结束

    def test_handle_ignores_send_failure(self):
        proxy = S.DnsProxy(5354, [], ["example.com"], lambda q, ips: None)
        proxy.sock = mock.Mock()
        proxy.sock.sendto.side_effect = OSError("broken pipe")
        answer = _dns_answer("api.example.com", [(1, bytes([1, 2, 3, 4]))])
        with mock.patch.object(S.DnsProxy, "forward", return_value=answer):
            proxy._handle(_dns_query("api.example.com"), ("127.0.0.1", 1234))

    def test_forward_returns_first_upstream_answer(self):
        answer = _dns_answer("a.com", [(1, bytes([1, 2, 3, 4]))])
        sock = _FakeSock(answer)
        proxy = S.DnsProxy(5354, ["192.0.2.1"], ["a.com"], lambda q, ips: None)
        with mock.patch.object(S.socket, "socket", return_value=sock), \
             mock.patch.object(S.select, "select", return_value=([sock], [], [])):
            self.assertEqual(proxy.forward(_dns_query("a.com")), answer)
        self.assertEqual(sock.sent[0][1], ("192.0.2.1", 53))
        self.assertTrue(sock.closed)


class TestDnsProxyForwardParallel(unittest.TestCase):
    """并行问所有上游、先到先用 —— 一份配置在中国和国外都能用的关键。"""

    def test_queries_all_upstreams_before_waiting(self):
        answer = _dns_answer("a.com", [(1, bytes([1, 2, 3, 4]))])
        socks = [_FakeSock(), _FakeSock(answer)]
        proxy = S.DnsProxy(5354, ["223.5.5.5", "1.1.1.1"], ["a.com"], lambda q, ips: None)
        with mock.patch.object(S.socket, "socket", side_effect=list(socks)), \
             mock.patch.object(S.select, "select", return_value=([socks[1]], [], [])):
            self.assertEqual(proxy.forward(_dns_query("a.com")), answer)
        # 两台都发了查询，没有「先等第一台超时」
        self.assertEqual([s.sent[0][1] for s in socks],
                         [("223.5.5.5", 53), ("1.1.1.1", 53)])
        self.assertTrue(all(s.closed for s in socks))

    def test_unsendable_upstream_is_skipped(self):
        answer = _dns_answer("a.com", [(1, bytes([1, 2, 3, 4]))])
        dead, live = _FakeSock(send_error=True), _FakeSock(answer)
        proxy = S.DnsProxy(5354, ["10.0.0.1", "1.1.1.1"], ["a.com"], lambda q, ips: None)
        with mock.patch.object(S.socket, "socket", side_effect=[dead, live]), \
             mock.patch.object(S.select, "select", return_value=([live], [], [])):
            self.assertEqual(proxy.forward(_dns_query("a.com")), answer)
        self.assertTrue(dead.closed)

    def test_socket_creation_failure_warns(self):
        r = _FakeReporter()
        proxy = S.DnsProxy(5354, ["192.0.2.1"], ["a.com"], lambda q, ips: None,
                           r, timeout=0.01)
        with mock.patch.object(S.socket, "socket", side_effect=OSError("no fd")):
            self.assertIsNone(proxy.forward(_dns_query("api.a.com")))
        self.assertIn("192.0.2.1", [m for k, m in r.lines if k == "warn"][0])

    def test_timeout_returns_none_and_closes_sockets(self):
        sock = _FakeSock()
        proxy = S.DnsProxy(5354, ["192.0.2.1"], ["a.com"], lambda q, ips: None,
                           timeout=0.01)
        with mock.patch.object(S.socket, "socket", return_value=sock), \
             mock.patch.object(S.select, "select", return_value=([], [], [])):
            self.assertIsNone(proxy.forward(_dns_query("a.com")))
        self.assertTrue(sock.closed)

    def test_expired_deadline_skips_select(self):
        sock = _FakeSock()
        proxy = S.DnsProxy(5354, ["192.0.2.1"], ["a.com"], lambda q, ips: None,
                           timeout=0)
        with mock.patch.object(S.socket, "socket", return_value=sock), \
             mock.patch.object(S.select, "select") as sel:
            self.assertIsNone(proxy.forward(_dns_query("a.com")))
        sel.assert_not_called()

    def test_readable_socket_that_errors_is_dropped(self):
        bad = _FakeSock(recv_error=True)
        proxy = S.DnsProxy(5354, ["192.0.2.1"], ["a.com"], lambda q, ips: None,
                           timeout=0.05)
        with mock.patch.object(S.socket, "socket", return_value=bad), \
             mock.patch.object(S.select, "select", return_value=([bad], [], [])):
            # 唯一的上游读失败 → 列表空掉，循环结束返回 None
            self.assertIsNone(proxy.forward(_dns_query("a.com")))
        self.assertTrue(bad.closed)


class TestSplitTunnelOnIps(unittest.TestCase):
    def test_new_ip_is_routed_and_announced(self):
        r = _FakeReporter()
        st = S.SplitTunnel({"routes": {"domains": ["example.com"]}}, r)
        captured = {}

        def fake_proxy_init(self, port, upstreams, domains, on_ips, reporter=None, **kw):
            captured["on_ips"] = on_ips
            self.port, self.domains, self.on_ips = port, domains, on_ips
            self.sock = None

        with mock.patch.object(S, "tun_for_ip", return_value="utun4"), \
             mock.patch.object(S.RouteTable, "add_host", side_effect=[True, False]), \
             mock.patch.object(S.DnsProxy, "__init__", fake_proxy_init), \
             mock.patch.object(S.DnsProxy, "start"), \
             mock.patch.object(S, "write_resolver_files"):
            st.start("10.8.0.6")
            captured["on_ips"]("api.example.com", ["1.1.1.1", "2.2.2.2"])

        infos = [m for k, m in r.lines if k == "info"]
        self.assertEqual(len([m for m in infos if "已加入 VPN 路由" in m]), 1)


if __name__ == "__main__":
    unittest.main()
