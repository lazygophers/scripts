"""disable-ipv6 / enable-ipv6 的实现 — 批量开关 macOS 网络服务的 IPv6。

`networksetup -listallnetworkservices` 首行是说明文字（"An asterisk (*) denotes…"），
要跳过；被停用的服务名前带 `*`，要剥掉才是真名。
"""
from __future__ import annotations

import os
import subprocess
import sys

from lib.notify import consume_debug, consume_dry_run, consume_help, consume_no_say
from lib.skills_help import consume_skills
from lib.ui import reporter


def _services() -> list[str]:
    out = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.lstrip("*").strip() for line in out.splitlines()[1:] if line.strip()]


def _apply(mode: str, done_label: str) -> int:
    r = reporter(stderr=True)
    if os.geteuid() != 0:
        r.err("需 sudo 运行")
        return 1
    for svc in _services():
        ok = subprocess.run(
            ["networksetup", mode, svc],
            capture_output=True, text=True,
        ).returncode == 0
        print(f"{done_label if ok else 'skip'}: {svc}")
    return 0


def _entry(name: str, description: str, mode: str, done_label: str) -> int:
    sys.argv = consume_dry_run(
        consume_help(
            consume_skills(consume_debug(consume_no_say(sys.argv)), description),
            description,
            usage=name,
        ),
        description,
    )
    return _apply(mode, done_label)


def disable_ipv6() -> int:
    """关闭本机所有网络服务的 IPv6"""
    return _entry("disable-ipv6", disable_ipv6.__doc__ or "", "-setv6off", "off")


def enable_ipv6() -> int:
    """开启本机所有网络服务的 IPv6（自动模式）"""
    return _entry("enable-ipv6", enable_ipv6.__doc__ or "", "-setv6automatic", "on")
