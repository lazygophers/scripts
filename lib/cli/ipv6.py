"""ipv6 — 开关本机所有网络服务的 IPv6（macOS networksetup，需 sudo，fire 重构）

`networksetup -listallnetworkservices` 首行是说明文字（"An asterisk (*) denotes…"），
要跳过；被停用的服务名前带 `*`，要剥掉才是真名。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from lib import privilege
from lib.fire_base import BaseCli, run_cli, timed_cli

# 提权时要原样重跑自己，所以这两个值必须在 fire 动 argv 之前就取好。
SCRIPT_PATH = pathlib.Path(sys.argv[0]).resolve()
ORIG_ARGV = list(sys.argv)


def _services() -> list[str]:
    out = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.lstrip("*").strip() for line in out.splitlines()[1:] if line.strip()]


class Ipv6Cli(BaseCli):
    """开关本机所有网络服务的 IPv6（需 sudo）"""

    def _apply(self, mode: str, done_label: str) -> int:
        # 一台机器上的网络服务可能有十几个，逐个改的过程里 sudo 授权可能过期；
        # 先把自己整个提成 root，后面每一条 networksetup 都不必再问。
        try:
            privilege.become_root(SCRIPT_PATH, ORIG_ARGV[1:])
        except privilege.NeedRoot as exc:
            self._r.err(str(exc))
            return 13
        for svc in _services():
            ok = subprocess.run(
                ["networksetup", mode, svc],
                capture_output=True, text=True,
            ).returncode == 0
            print(f"{done_label if ok else 'skip'}: {svc}")
        return 0

    @timed_cli
    def enable(self):
        """开启本机所有网络服务的 IPv6（自动模式）"""
        return self._apply("-setv6automatic", "on")

    @timed_cli
    def disable(self):
        """关闭本机所有网络服务的 IPv6"""
        return self._apply("-setv6off", "off")


def main():
    run_cli(Ipv6Cli())
