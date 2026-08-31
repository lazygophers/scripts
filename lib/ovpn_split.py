"""按域名分流：本地 DNS 代理发现 IP → 写进系统路由表。

为什么需要 DNS 代理：系统路由表每一行只有「目标网段 / 下一跳 / 网卡」，
按 IP 前缀匹配，没有任何字段能放域名；数据包本身也不携带域名。所以
`*.example.com` 必须先变成一串具体 IP，而 DNS 协议不支持枚举子域 ——
只能反过来，等应用真的去查某个子域时把答案截下来。

macOS 原生支持按域名分流 DNS（man 5 resolver）：`/etc/resolver/example.com`
里写 `nameserver 127.0.0.1` + `port 5354`，系统就把 example.com 及其所有
子域的查询发到本地代理。代理转发给真正的上游 DNS，拿到答案后先
`route add -host <ip> -interface utunN`，再把答案原样还给应用。

清理策略：resolver 文件带标记注释，`ovpn connect` 启动时先扫一遍删掉上次
残留的（被 kill -9 时来不及清理），正常退出和 `ovpn disconnect` 也各清一次。
"""

from __future__ import annotations

import ipaddress
import pathlib
import re
import select
import socket
import subprocess
import threading
import time

RESOLVER_DIR = pathlib.Path("/etc/resolver")

# 写进 resolver 文件的标记：靠它认出哪些是本工具写的，好清理残留
RESOLVER_MARKER = "# managed-by: lazygophers-ovpn"

DEFAULT_DNS_PORT = 5354


# ---------------------------------------------------------------- 域名规则

def normalize_domain(pattern: str) -> str:
    """把用户写的规则归一成 resolver 文件名。

    `*.example.com` / `.example.com` / `example.com` 都归到 `example.com` ——
    macOS 的 resolver 文件对该域名及其所有子域生效，不需要区分。
    """
    d = (pattern or "").strip().lower().rstrip(".")
    d = re.sub(r"^\*\.", "", d)
    return d.lstrip(".")


def domain_matches(qname: str, domain: str) -> bool:
    """DNS 查询名是否落在某条域名规则下（域名本身或任意子域）。"""
    q = (qname or "").lower().rstrip(".")
    d = (domain or "").lower().rstrip(".")
    return bool(d) and (q == d or q.endswith("." + d))


# ---------------------------------------------------------------- DNS 报文

def parse_question_name(msg: bytes) -> str | None:
    """从 DNS 报文里取第一个问题的域名。格式非法返回 None。"""
    if len(msg) < 12:
        return None
    labels = []
    i = 12
    while i < len(msg):
        n = msg[i]
        if n == 0:
            return ".".join(labels)
        if n & 0xC0:  # 问题段不该出现压缩指针
            return None
        i += 1
        if i + n > len(msg):
            return None
        labels.append(msg[i:i + n].decode("ascii", "replace"))
        i += n
    return None


_QTYPES = {1: "A", 28: "AAAA", 5: "CNAME", 12: "PTR", 15: "MX", 16: "TXT", 33: "SRV", 65: "HTTPS"}


def question_label(msg: bytes) -> str:
    """把报文的问题段描述成 `域名 (A)` 这样，日志里用来指明是哪个查询。"""
    name = parse_question_name(msg)
    if not name:
        return "未知查询"
    i = _skip_name(msg, 12)
    if i + 2 > len(msg):
        return name
    qtype = int.from_bytes(msg[i:i + 2], "big")
    return f"{name} ({_QTYPES.get(qtype, f'TYPE{qtype}')})"


def _skip_name(msg: bytes, i: int) -> int:
    """跳过一个（可能被压缩的）域名，返回下一个字节的偏移。"""
    while i < len(msg):
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:  # 压缩指针占 2 字节
            return i + 2
        i += 1 + n
    return i


def parse_answer_ips(msg: bytes) -> list[str]:
    """从 DNS 响应里取出所有 A / AAAA 记录的 IP。解析不动就返回空列表。"""
    if len(msg) < 12:
        return []
    qd = int.from_bytes(msg[4:6], "big")
    an = int.from_bytes(msg[6:8], "big")
    i = 12
    for _ in range(qd):
        i = _skip_name(msg, i) + 4  # 跳过 QTYPE + QCLASS
    ips = []
    for _ in range(an):
        i = _skip_name(msg, i)
        if i + 10 > len(msg):
            break
        rtype = int.from_bytes(msg[i:i + 2], "big")
        rdlen = int.from_bytes(msg[i + 8:i + 10], "big")
        rdata = msg[i + 10:i + 10 + rdlen]
        i += 10 + rdlen
        if rtype == 1 and rdlen == 4:
            ips.append(str(ipaddress.IPv4Address(rdata)))
        elif rtype == 28 and rdlen == 16:
            ips.append(str(ipaddress.IPv6Address(rdata)))
    return ips


# 兜底上游：既没配 dns_upstream、VPN 也没 push、/etc/resolv.conf 还空的时候用。
# 国内 + 国外各两台并列 —— forward() 是并行问、先到先用，在中国 1.1.1.1/8.8.8.8
# 不通只是不答，223.5.5.5 照样秒回；在国外反过来。所以同一份配置两地都能用，
# 不需要按地区切换。
FALLBACK_NAMESERVERS = ["223.5.5.5", "119.29.29.29", "1.1.1.1", "8.8.8.8"]


def system_nameservers() -> list[str]:
    """读 /etc/resolv.conf 里的上游 DNS，排除回环地址（避免打回自己造成死循环）。"""
    servers = []
    try:
        text = pathlib.Path("/etc/resolv.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            try:
                addr = ipaddress.ip_address(parts[1])
            except ValueError:
                continue
            if not addr.is_loopback:
                servers.append(str(addr))
    return servers or list(FALLBACK_NAMESERVERS)


# ---------------------------------------------------------------- 路由表

def tun_for_ip(local_ip: str) -> str | None:
    """按 openvpn 报的本地 IP 反查它落在哪个 utun 网卡上。"""
    from lib.ovpn import tun_interfaces

    for name, ip in tun_interfaces():
        if ip == local_ip:
            return name
    return None


class RouteTable:
    """往系统路由表里加/删主机路由，记账以便退出时收干净。"""

    def __init__(self, interface: str, reporter=None) -> None:
        self.interface = interface
        self.reporter = reporter
        self.added: set[str] = set()
        self._lock = threading.Lock()

    def add_host(self, ip: str) -> bool:
        """`route add -host <ip> -interface <utun>`。已加过或加失败返回 False。"""
        with self._lock:
            if ip in self.added:
                return False
            self.added.add(ip)
        family = "-inet6" if ":" in ip else "-inet"
        cmd = ["sudo", "route", "-n", "add", family, "-host", ip, "-interface", self.interface]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # 路由已存在（EEXIST）不算错，其它情况报出来
            if "File exists" not in (r.stderr or ""):
                if self.reporter:
                    self.reporter.warn(f"给 {ip} 加 VPN 路由失败；这个 IP 可能走不到 VPN。系统返回: {(r.stderr or '').strip()}")
                with self._lock:
                    self.added.discard(ip)
                return False
        elif self.reporter:
            self.reporter.step(f"已让 {ip} 走 VPN 网卡 {self.interface}")
        return True

    def add_network(self, cidr: str) -> bool:
        """`route add -net <cidr> -interface <utun>`，用于配置里写死的网段。"""
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            if self.reporter:
                self.reporter.warn(f"分流网段写错，已跳过: {cidr}。例子: 10.8.0.0/16")
            return False
        family = "-inet6" if net.version == 6 else "-inet"
        cmd = ["sudo", "route", "-n", "add", family, "-net", str(net),
               "-interface", self.interface]
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = r.returncode == 0 or "File exists" in (r.stderr or "")
        if ok:
            with self._lock:
                self.added.add(str(net))
            if self.reporter:
                self.reporter.step(f"已让 {net} 走 VPN 网卡 {self.interface}")
        elif self.reporter:
            self.reporter.warn(f"给分流网段 {net} 加 VPN 路由失败。系统返回: {(r.stderr or '').strip()}")
        return ok

    def flush(self) -> None:
        """删掉本对象加过的所有路由。

        网卡消失时内核会自动清掉相关路由，所以这里的失败一律忽略。
        """
        with self._lock:
            targets = sorted(self.added)
            self.added.clear()
        for t in targets:
            family = "-inet6" if ":" in t else "-inet"
            kind = "-net" if "/" in t else "-host"
            subprocess.run(["sudo", "route", "-n", "delete", family, kind, t],
                           capture_output=True, text=True)


# ---------------------------------------------------------------- resolver 文件

def resolver_file_content(port: int) -> str:
    return f"{RESOLVER_MARKER}\nnameserver 127.0.0.1\nport {port}\n"


def is_ours(path: pathlib.Path) -> bool:
    """这个 resolver 文件是不是本工具写的（靠首行标记判断）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace").startswith(RESOLVER_MARKER)
    except OSError:
        return False


def stale_resolver_files(resolver_dir: pathlib.Path = RESOLVER_DIR) -> list[pathlib.Path]:
    """列出 /etc/resolver 下本工具写的残留文件（上次被 kill -9 留下的）。"""
    if not resolver_dir.is_dir():
        return []
    return sorted(p for p in resolver_dir.iterdir() if p.is_file() and is_ours(p))


def clean_resolver_files(reporter=None, resolver_dir: pathlib.Path = RESOLVER_DIR) -> int:
    """删掉所有本工具写的 resolver 文件，返回删掉的个数。"""
    stale = stale_resolver_files(resolver_dir)
    if not stale:
        return 0
    subprocess.run(["sudo", "rm", "-f", *[str(p) for p in stale]],
                   capture_output=True, text=True)
    if reporter:
        reporter.step(f"已清理上次残留的 DNS 分流文件 {len(stale)} 个: {', '.join(p.name for p in stale)}")
    return len(stale)


def write_resolver_files(domains: list[str], port: int, reporter=None,
                         resolver_dir: pathlib.Path = RESOLVER_DIR) -> None:
    """为每个域名写一个 resolver 文件，把它的 DNS 指到本地代理。需要 sudo。"""
    if not domains:
        return
    subprocess.run(["sudo", "mkdir", "-p", str(resolver_dir)], capture_output=True, text=True)
    content = resolver_file_content(port)
    for d in domains:
        target = resolver_dir / d
        r = subprocess.run(["sudo", "tee", str(target)], input=content,
                           capture_output=True, text=True)
        if r.returncode != 0 and reporter:
            reporter.warn(f"写入 DNS 分流文件失败: {target}。系统返回: {(r.stderr or '').strip()}")
    if reporter:
        reporter.step(f"DNS 分流已打开。以下域名会先走 VPN DNS: {', '.join(domains)}")


# ---------------------------------------------------------------- DNS 代理

class DnsProxy:
    """UDP DNS 代理：转发给上游，并把答案里的 IP 喂给路由表。

    只有被 /etc/resolver 指过来的域名会到这里，所以不做域名过滤也不会影响
    其它流量；on_ips 回调仍然按规则再确认一次，避免上游 CNAME 指到别处。
    """

    def __init__(self, port: int, upstreams, domains: list[str],
                 on_ips, reporter=None, *, timeout: float = 5.0) -> None:
        self.port = port
        # 可以传一个 callable：上游可能在代理起来之后才由服务端 push 过来
        self._upstreams = upstreams
        self.domains = domains
        self.on_ips = on_ips
        self.reporter = reporter
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def upstreams(self) -> list[str]:
        return list(self._upstreams() if callable(self._upstreams) else self._upstreams)

    def start(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.sock:
            self.sock.close()
            self.sock = None

    def _serve(self) -> None:
        assert self.sock is not None
        while not self._stop.is_set():
            try:
                query, client = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(query, client), daemon=True).start()

    def _handle(self, query: bytes, client) -> None:
        answer = self.forward(query)
        if answer is None:
            return
        qname = parse_question_name(query) or ""
        if any(domain_matches(qname, d) for d in self.domains):
            ips = parse_answer_ips(answer)
            if ips:
                self.on_ips(qname, ips)
        try:
            if self.sock:
                self.sock.sendto(answer, client)
        except OSError:
            pass

    def forward(self, query: bytes) -> bytes | None:
        """同时问所有上游 DNS，谁先答上来就用谁。全部超时返回 None。

        并行而不是逐个问，是为了让同一份配置在中国和国外都能用：兜底上游里
        国内国外的 DNS 并列，在中国境外那几台只是不响应，不会拖慢境内那几台。
        只在同一梯队内部并行（见 SplitTunnel.upstreams 的优先级），不混梯队 ——
        分流域名是内网域名，公共 DNS 抢答会给出错误的公网 IP。
        """
        label = question_label(query)
        upstreams = self.upstreams
        socks: list[socket.socket] = []
        try:
            for up in upstreams:
                s = None
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setblocking(False)
                    s.sendto(query, (up, 53))
                except OSError:
                    if s is not None:
                        s.close()
                    continue
                socks.append(s)
            deadline = time.monotonic() + self.timeout
            while socks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select(socks, [], [], remaining)
                if not ready:
                    break
                for s in ready:
                    try:
                        return s.recvfrom(4096)[0]
                    except OSError:
                        socks.remove(s)
                        s.close()
        finally:
            for s in socks:
                s.close()
        if self.reporter:
            self.reporter.warn(f"查不到 {label}：当前 DNS 都没回应（{', '.join(upstreams)}）。下一步: 确认 VPN 已连接，或跑 `ovpn connect --verbose` 看 DNS 细节")
        return None


# ---------------------------------------------------------------- 编排

class SplitTunnel:
    """把上面几块拼起来：连上之后启动，断开时收干净。"""

    def __init__(self, cfg: dict, reporter) -> None:
        routes = cfg.get("routes") or {}
        raw_domains = routes.get("domains") or []
        self.domains = sorted({normalize_domain(d) for d in raw_domains if normalize_domain(d)})
        self.cidrs = [str(c) for c in (routes.get("cidrs") or [])]
        self.port = int(cfg.get("dns_port") or DEFAULT_DNS_PORT)
        self.configured_upstreams = [str(x) for x in (cfg.get("dns_upstream") or [])]
        self.pushed_dns: list[str] = []
        self.reporter = reporter
        self.table: RouteTable | None = None
        self.proxy: DnsProxy | None = None

    def note_pushed_dns(self, servers: list[str]) -> None:
        """记下服务端 push 的 DNS。命中分流的域名优先问它，未命中的仍走系统 DNS。"""
        for ip in servers:
            if ip not in self.pushed_dns:
                self.pushed_dns.append(ip)

    @property
    def upstreams(self) -> list[str]:
        """上游优先级：配置里写死的 > VPN push 的 > 系统 resolv.conf。"""
        return self.configured_upstreams or self.pushed_dns or system_nameservers()

    @property
    def enabled(self) -> bool:
        return bool(self.domains or self.cidrs)

    def start(self, local_ip: str) -> None:
        """openvpn 报 CONNECTED 之后调用：定位 utun、加固定网段、起 DNS 代理。"""
        iface = tun_for_ip(local_ip)
        if not iface:
            self.reporter.err(f"VPN 已连上，但找不到本机 VPN 网卡（VPN IP: {local_ip}），所以分流没有打开。下一步: 跑 `ifconfig | grep -A2 utun` 检查 VPN 网卡")
            return
        self.table = RouteTable(iface, self.reporter)
        for c in self.cidrs:
            self.table.add_network(c)
        # push 过来的 DNS 一般只在 VPN 内部可达；--route-nopull 下没人给它加路由
        for ip in self.pushed_dns:
            self.table.add_host(ip)
        if not self.domains:
            return
        self.reporter.info(f"分流域名会先问这些 DNS: {', '.join(self.upstreams)}")

        def on_ips(qname: str, ips: list[str]) -> None:
            assert self.table is not None
            for ip in ips:
                if self.table.add_host(ip) and self.reporter:
                    self.reporter.info(f"{qname} 解析到 {ip}，已让这个 IP 走 VPN")

        self.proxy = DnsProxy(self.port, lambda: self.upstreams, self.domains, on_ips, self.reporter)
        try:
            self.proxy.start()
        except OSError as e:
            self.reporter.err(f"本地 DNS 代理启动失败（127.0.0.1:{self.port}）。分流域名可能打不开。系统返回: {e}")
            self.proxy = None
            return
        write_resolver_files(self.domains, self.port, self.reporter)

    def stop(self) -> None:
        """断开时收尾：删 resolver 文件、停代理、删路由。顺序不能反。"""
        if self.domains:
            clean_resolver_files(self.reporter)
        if self.proxy:
            self.proxy.stop()
            self.proxy = None
        if self.table:
            self.table.flush()
            self.table = None
