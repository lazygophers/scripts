"""网络信息查询：内网 IP + 网络类型。"""

from __future__ import annotations

import re
from typing import Any

from lib.exec import run

# 公网 IP API（仅 import 自查时引用，不再使用）—— 保留供外部 if needed
WAN_API_IPINFO = "https://ipinfo.io/json"
WAN_API_IPAPI = "http://ip-api.com/json/?fields=status,country,regionName,city,query,as,org"


def lan_ip() -> str | None:
    """获取本机内网 IP（macOS 优先 ipconfig，兜底 hostname -I）。"""
    for iface in ("en0", "en1", "en2"):
        p = run(["ipconfig", "getifaddr", iface], check=False, capture_output=True)
        out = (p.stdout or "").strip()
        if out and _looks_like_ip(out):
            return out
    p = run(["hostname", "-I"], check=False, capture_output=True)
    out = (p.stdout or "").strip().split()
    if out and _looks_like_ip(out[0]):
        return out[0]
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def net_type() -> list[str]:
    """返回所有活动网络接口类型列表（Wi-Fi / Ethernet / USB / Thunderbolt / Other）。

    遍历 networksetup -listallhardwareports 找有 IP 的接口 → 映射类型。
    按 Wi-Fi > USB > Ethernet > Thunderbolt 优先级排序，无活动接口时返回 ["None"]。
    """
    p = run(["networksetup", "-listallhardwareports"], check=False, capture_output=True)
    out = (p.stdout or "")
    if not out:
        return ["Unknown"]

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

    priority = [
        ("Wi-Fi", "Wi-Fi"),
        ("USB", "USB Ethernet"),
        ("Ethernet", "Ethernet"),
        ("Thunderbolt Bridge", "Thunderbolt Bridge"),
        ("Thunderbolt", "Thunderbolt"),
    ]
    seen_labels: set[str] = set()
    active_by_label: dict[str, str] = {}
    other_ports: list[str] = []
    for port, dev in blocks:
        ip = _ip_of(dev)
        if not ip:
            continue
        port_lc = port.lower()
        matched_label: str | None = None
        for keyword, label in priority:
            pat = re.compile(rf"(^|\W){re.escape(keyword.lower())}($|\W)")
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
    active = [active_by_label[label] for _, label in priority if label in active_by_label]
    active.extend(other_ports)
    return active or ["None"]


def _ip_of(bsd: str) -> str | None:
    """查某个 BSD 设备名（如 en0）的内网 IP。"""
    p = run(["ipconfig", "getifaddr", bsd], check=False, capture_output=True)
    out = (p.stdout or "").strip()
    return out if _looks_like_ip(out) else None


def is_hotspot_wifi() -> bool:
    """当前是否处于热点共享的网络（iPhone/Android Personal Hotspot 模式）。

    macOS 上 iPhone 个人热点会创建 `bridge1..bridge99` 的 bridge 接口并分配 IPv4
    （典型 172.20.0.0/16 段）。判定：
      1) `ifconfig` 里存在 bridge1..bridge99 且有 IPv4；
      2) `netstat -rn` 里 `default` 路由真正指向该 bridge 接口（未带 `!` reject 标记）。
    仅两者都满足才视为热点，避免 bridge 残留但未在 active path 上时误判。
    """
    p = run(["ifconfig"], check=False, capture_output=True)
    out = (p.stdout or "")
    bridge_with_ip: str | None = None
    cur: str | None = None
    for raw in out.splitlines():
        s = raw.strip()
        if raw and not raw[0].isspace() and ":" in s:
            name = s.split(":", 1)[0].strip()
            cur = name
        if cur and cur.startswith("bridge") and cur != "bridge0" and s.startswith("inet "):
            ip = s.split()[1]
            if _looks_like_ip(ip):
                bridge_with_ip = cur
                break
    if not bridge_with_ip:
        return False

    p = run(["netstat", "-rn"], check=False, capture_output=True)
    for line in (p.stdout or "").splitlines():
        s = line.strip()
        parts = s.split()
        if not parts or parts[0] != "default":
            continue
        if bridge_with_ip not in parts:
            return False
        # macOS flags 可能在接口名后：default ... bridge100 !
        return "!" not in parts
    return False


def _looks_like_ip(s: str) -> bool:
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s))