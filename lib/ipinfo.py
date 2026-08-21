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


def wan_info(source: str = "ipinfo", *, proxy: str | None = None) -> dict[str, Any] | None:
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


def proxy_info(source: str = "ipinfo") -> dict[str, Any] | None:
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


def net_type() -> str:
    """返回当前主网络类型：Wi-Fi / Ethernet / USB Ethernet / Thunderbolt / None。

    遍历 networksetup -listallhardwareports 找有 IP 的接口 → 映射类型。
    """
    p = run(["networksetup", "-listallhardwareports"], check=False, capture_output=True)
    out = (p.stdout or "")
    if not out:
        return "Unknown"

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

    # 找有 IP 的接口（按活动顺序：Wi-Fi > USB LAN > Ethernet > Thunderbolt Bridge）
    priority = [
        ("Wi-Fi", "Wi-Fi"),
        ("USB", "USB Ethernet"),
        ("Ethernet", "Ethernet"),
        ("Thunderbolt Bridge", "Thunderbolt Bridge"),
        ("Thunderbolt", "Thunderbolt"),
    ]
    for port_name, label in priority:
        for port, dev in blocks:
            if port_name.lower() in port.lower():
                ip = _ip_of(dev)
                if ip:
                    return label
    # 兜底：找任意有 IP 的接口
    for port, dev in blocks:
        if _ip_of(dev):
            return port
    return "Unknown"


def _ip_of(bsd: str) -> str | None:
    """查某个 BSD 设备名（如 en0）的内网 IP。"""
    p = run(["ipconfig", "getifaddr", bsd], check=False, capture_output=True)
    out = (p.stdout or "").strip()
    return out if _looks_like_ip(out) else None


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
