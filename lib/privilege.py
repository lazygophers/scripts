"""需要 root 的命令怎么拿权限，以及怎么把它握到命令结束。

两条路，**优先第一条**：

1. `become_root()` —— 不是 root 就用 sudo 把自己原样重跑一遍。此后整个进程都是 root，
   中途再要动系统什么东西都不必回头找 sudo，也就不存在「sudo 授权过期了」这回事。
   密码只在最开始问一次。
2. `keep_sudo_alive()` —— 进程必须保持普通用户身份（比如要读用户的 `~`、要写用户属主
   的文件），但过程中会反复 `sudo` 干活。先 `sudo -v` 拿一次授权，然后每分钟在后台
   刷新一次，直到进程退出。

sudo 的授权默认 5 分钟过期（`timestamp_timeout`，见 `man 5 sudoers`）。一条跑了十分钟
的命令，如果中途才第一次 `sudo`，就会在半路弹密码——脚本在后台跑时那等于直接失败，
而且往往已经做了一半。这个模块存在的理由就是这个。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading

# 刷新间隔。sudo 默认 5 分钟过期，1 分钟刷一次留足余量，也不至于频繁得离谱。
REFRESH_SECONDS = 60.0


class NeedRoot(Exception):
    """要 root 但拿不到：sudo 不在、用户取消、或者没有 TTY 可输密码。"""


def is_root() -> bool:
    return os.geteuid() == 0


def sudo_argv(script: pathlib.Path, argv: list[str], extra: list[str] | None = None) -> list[str]:
    """拼出「用 sudo 重跑自己」的命令行。

    解释器写成绝对路径（`sys.executable`）：sudo 默认 `env_reset`，PATH 里未必有
    mise / venv 里的那个 python。`extra` 给调用方补参数用——archery 要把配置路径显式
    带过去，因为 root 的 `$HOME` 是 /var/root。
    """
    return ["sudo", sys.executable, str(script), *argv, *(extra or [])]


def become_root(script: pathlib.Path, argv: list[str], extra: list[str] | None = None) -> None:
    """已经是 root 就直接返回；不是就用 sudo 重跑自己（`execvp`，不返回）。"""
    if is_root():
        return
    try:
        os.execvp("sudo", sudo_argv(script, argv, extra))
    except OSError as exc:
        raise NeedRoot(f"这条命令需要 root，但起不了 sudo: {exc}") from exc


def keep_sudo_alive(interval: float = REFRESH_SECONDS) -> threading.Event | None:
    """先拿一次 sudo 授权，然后在后台一直续着，直到进程结束。

    给那些必须以普通用户身份跑、但过程中要反复 `sudo` 的命令用。返回的 Event 可以
    用来提前停掉续期；线程是 daemon，进程退出时自己消失，调用方不必收尾。

    已经是 root 就什么都不用做。拿不到授权（用户按了 Ctrl-C、没有 TTY）抛 `NeedRoot`，
    **不静默降级**——真到动手那一步才失败，代价是做了一半。
    """
    if is_root():
        return None
    try:
        done = subprocess.run(["sudo", "-v"], check=False)
    except OSError as exc:
        raise NeedRoot(f"这条命令需要 root，但起不了 sudo: {exc}") from exc
    if done.returncode != 0:
        raise NeedRoot("没拿到 sudo 授权（密码不对或已取消）")

    stop = threading.Event()

    def refresh() -> None:
        # `sudo -n -v` 只刷新时间戳，不会弹密码：刷不动就是授权真没了，那时也没什么
        # 可做的，留给下一次真正的 sudo 去报错。
        while not stop.wait(interval):
            subprocess.run(["sudo", "-n", "-v"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    threading.Thread(target=refresh, name="sudo-keepalive", daemon=True).start()
    return stop


__all__ = ["NeedRoot", "REFRESH_SECONDS", "become_root", "is_root", "keep_sudo_alive", "sudo_argv"]
