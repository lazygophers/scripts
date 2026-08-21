"""网络信息查询：内网 IP / 公网 IP+地区 / 代理出口 / 网络类型。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from typing import Any

from lib.exec import NET_TIMEOUT, run
from lib.ui import Reporter, reporter

# 公网 IP API（无需密钥）
WAN_API_IPINFO = "https://ipinfo.io/json"
WAN_API_IPAPI = "http://ip-api.com/json/?fields=status,country,regionName,city,query,as,org"


def lan_ip() -> str | None:
    """获取本机内网 IP（macOS 优先 ipconfig，兜底 hostname -I）。"""
    # 1) ipconfig getifaddr en0 — 主流 Wi-Fi 接口
    for iface in ("en0", "en1", "en2"):
        p = run(["ipconfig", "getifaddr", iface], check=False, capture_output=True)
        out = (p.stdout or "").strip()
        if out and _looks_like_ip(out):
            return out
    # 2) 兜底：hostname -I（macOS 不一定有，Linux 通杀）
    p = run(["hostname", "-I"], check=False, capture_output=True)
    out = (p.stdout or "").strip().split()
    if out and _looks_like_ip(out[0]):
        return out[0]
    # 3) 兜底：socket 连外网时拿本地 IP（不一定准确但不会 None）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def wan_info(source: str = "ip-api", *, proxy: str | None = None) -> dict[str, Any] | None:
    """查公网 IP + 地区。proxy 非空时强制走代理。

    返回 dict:
      - source=ipinfo: {ip, country, region, city, org, ...}（原样透传）
      - source=ip-api: 同上字段（统一为 ip/country/region/city/org）
    失败返回 None。
    """
    url = WAN_API_IPINFO if source == "ipinfo" else WAN_API_IPAPI
    cmd = ["curl", "-sS", "--max-time", str(int(NET_TIMEOUT))]
    if proxy:
        cmd += ["-x", proxy]
    cmd += [url]
    p = run(cmd, check=False, capture_output=True)
    out = (p.stdout or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    # 统一字段
    if source == "ip-api":
        if data.get("status") != "success":
            return None
        return {
            "ip": data.get("query"),
            "country": data.get("country"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "org": data.get("org") or data.get("as"),
        }
    # ipinfo 原字段
    return {
        "ip": data.get("ip"),
        "country": data.get("country"),
        "region": data.get("region"),
        "city": data.get("city"),
        "org": data.get("org"),
    }


def proxy_info(source: str = "ip-api") -> dict[str, Any] | None:
    """通过 $HTTPS_PROXY / $HTTP_PROXY 查出口 IP。无代理返回 None。"""
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if not proxy:
        return None
    return wan_info(source, proxy=proxy)


def net_type() -> list[str]:
    """返回所有活动网络接口类型列表（Wi-Fi / Ethernet / USB / Thunderbolt / Other）。

    遍历 networksetup -listallhardwareports 找有 IP 的接口 → 映射类型。
    按 Wi-Fi > USB > Ethernet > Thunderbolt 优先级排序，可能为空（无活动接口时返回 ["None"]）。
    """
    p = run(["networksetup", "-listallhardwareports"], check=False, capture_output=True)
    out = (p.stdout or "")
    if not out:
        return ["Unknown"]

    # 解析: Hardware Port: <name>\nDevice: <bsd>
    blocks: list[tuple[str, str]] = []
    cur_port: str | None = None
    cur_dev: str | None = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Hardware Port:"):
            if cur_port and cur_dev:
                blocks.append((cur_port, cur_dev))
            cur_port = s.split(":", 1)[1].strip()
            cur_dev = None
        elif s.startswith("Device:"):
            cur_dev = s.split(":", 1)[1].strip()
    if cur_port and cur_dev:
        blocks.append((cur_port, cur_dev))

    # 按活动顺序筛接口：Wi-Fi > USB > Ethernet > Thunderbolt > Other
    # 同类型多个接口（如 en0 + en1 都是 Wi-Fi）只算一次
    priority = [
        ("Wi-Fi", "Wi-Fi"),
        ("USB", "USB Ethernet"),
        ("Ethernet", "Ethernet"),
        ("Thunderbolt Bridge", "Thunderbolt Bridge"),
        ("Thunderbolt", "Thunderbolt"),
    ]
    # 按 activity 顺序识别：Wi-Fi > USB > Ethernet > Thunderbolt > Other
    # 同类型多个接口（en0+en1 都是 Wi-Fi）只算一次。
    # 用词边界匹配避免 "Ethernet Adapter (en3)" 误匹配 "Ethernet"。
    import re as _re
    seen_labels: set[str] = set()
    active_by_label: dict[str, str] = {}  # label → 出现顺序无关；按 priority 重排
    other_ports: list[str] = []
    for port, dev in blocks:
        ip = _ip_of(dev)
        if not ip:
            continue
        port_lc = port.lower()
        matched_label: str | None = None
        for keyword, label in priority:
            pat = _re.compile(rf"(^|\W){_re.escape(keyword.lower())}($|\W)")
            if pat.search(port_lc):
                matched_label = label
                break
        if matched_label:
            if matched_label not in seen_labels:
                seen_labels.add(matched_label)
                active_by_label[matched_label] = matched_label
        else:
            if port not in other_ports:
                other_ports.append(port)
    # 按 priority 顺序输出 + 未识别端口
    active = [active_by_label[label] for _, label in priority if label in active_by_label]
    active.extend(other_ports)
    return active or ["None"]


def _ip_of(bsd: str) -> str | None:
    """查某个 BSD 设备名（如 en0）的内网 IP。"""
    p = run(["ipconfig", "getifaddr", bsd], check=False, capture_output=True)
    out = (p.stdout or "").strip()
    return out if _looks_like_ip(out) else None


# 常见热点 SSID 关键词（大小写不敏感）：iPhone 个人热点、Android 热点、便携式热点等
_HOTSPOT_SSID_HINTS = (
    "iphone", "ipad", "android", "galaxy", "pixel",
    "huawei", "honor", "xiaomi", "redmi", "oneplus",
    "personal hotspot", "hotspot", "移动热点", "personal",
)


def is_hotspot_wifi() -> bool:
    """当前是否处于热点共享的网络（iPhone/Android Personal Hotspot 模式）。

    macOS 上 iPhone 个人热点会创建 `bridge1..bridge99` 的 bridge 接口并分配 IPv4
    （典型 172.20.0.0/16 段）。检查方式：`ifconfig bridge100/...` 有 IPv4 即视为热点。
    不依赖 SSID 字符串。
    """
    p = run(["ifconfig"], check=False, capture_output=True)
    out = (p.stdout or "")
    # 解析每个接口段，找 bridge1..bridge99 且有 inet 行（IPv4）
    cur: str | None = None
    for raw in out.splitlines():
        s = raw.strip()
        # 段头：行首不是缩进（无前导 tab/space），且像 "<name>: flags=..." 形式
        if raw and not raw[0].isspace() and ":" in s:
            name = s.split(":", 1)[0].strip()
            cur = name
        # IPv4 行带 "inet " 且不是 "inet6"
        if cur and cur.startswith("bridge") and s.startswith("inet "):
            ip = s.split()[1]
            if _looks_like_ip(ip):
                # 排除 Thunderbolt Bridge (bridge0)
                if cur == "bridge0":
                    continue
                return True
    return False


def _looks_like_ip(s: str) -> bool:
    import re
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s))


def render(rows: list[tuple[str, str]], *, json_mode: bool = False) -> str:
    """把 (label, value) 列表渲染为人读或 JSON 字符串。"""
    if json_mode:
        return json.dumps({k: v for k, v in rows}, ensure_ascii=False, indent=2)
    out_lines = []
    max_label = max(len(k) for k, _ in rows) if rows else 0
    for label, value in rows:
        out_lines.append(f"{label:<{max_label}}  {value}")
    return "\n".join(out_lines)
