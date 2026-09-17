"""把 bridge 装成开机自启的系统服务（macOS launchd / Linux systemd user / Windows 启动项）。

没有这层也能用：任何一条 `browse` 指令都会把 bridge 拉起来。装成服务换来的是另一件
事——**浏览器一开就连得上**。不装的话，扩展在你敲第一条命令之前一直连不上，面板上
就是一串重连失败；装了之后 bridge 一直在，扩展开机即连。

服务模式下把空闲退出关掉（`--idle-timeout 0`）：按需模式闲 30 分钟自退是对的，常驻
服务再自退就等于服务一直在重启。

三个平台的做法都取自各自的官方文档：

- launchd：`~/Library/LaunchAgents/<Label>.plist` + `launchctl bootstrap gui/<uid>`
  <https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html>
- systemd user：`~/.config/systemd/user/<name>.service` + `systemctl --user enable --now`
  <https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html>
- Windows：用户启动文件夹里放一个 `.cmd`（没有守护重启，Windows 上要那个得管理员装服务）
  <https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid>

这里只生成文件和拼命令，真正执行交给调用方传进来的 `runner`，所以整套逻辑单测得动，
不必真往机器上装一个服务。
"""

from __future__ import annotations

import os
import pathlib
import subprocess

LABEL = "com.lazygophers.browse.bridge"
UNIT_NAME = "browse-bridge.service"


def unit_path(home: pathlib.Path, plat: str) -> pathlib.Path:
    """服务描述文件的落点。三个平台各一处，都在用户目录下，不需要管理员权限。"""
    if plat == "darwin":
        return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if plat == "win32":
        return (home / "AppData" / "Roaming" / "Microsoft" / "Windows"
                / "Start Menu" / "Programs" / "Startup" / "browse-bridge.cmd")
    return home / ".config" / "systemd" / "user" / UNIT_NAME


def unit_text(plat: str, exe: str, socket: str, log: str) -> str:
    """服务描述文件的内容。`exe` 是 browse 可执行文件的绝对路径。"""
    if plat == "darwin":
        args = "".join(f"    <string>{part}</string>\n" for part in
                       (exe, "bridge", "run", "--socket", socket, "--idle-timeout", "0"))
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            f"  <key>Label</key><string>{LABEL}</string>\n"
            "  <key>ProgramArguments</key>\n"
            f"  <array>\n{args}  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "  <key>KeepAlive</key><true/>\n"
            f"  <key>StandardOutPath</key><string>{log}</string>\n"
            f"  <key>StandardErrorPath</key><string>{log}</string>\n"
            "</dict>\n"
            "</plist>\n"
        )
    if plat == "win32":
        return f'@echo off\r\nstart "" /b "{exe}" bridge run --socket "{socket}" --idle-timeout 0\r\n'
    return (
        "[Unit]\n"
        "Description=lazygophers browse bridge\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exe} bridge run --socket {socket} --idle-timeout 0\n"
        "Restart=always\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _enable(plat: str, path: pathlib.Path) -> list[list[str]]:
    """写完文件之后要跑的命令。Windows 的启动项放进去就算装好，没有命令。"""
    if plat == "darwin":
        target = f"gui/{os.getuid()}"
        # 先 bootout 再 bootstrap：装过一次之后再装，bootstrap 会因为已加载而失败
        return [["launchctl", "bootout", f"{target}/{LABEL}"],
                ["launchctl", "bootstrap", target, str(path)]]
    if plat == "win32":
        return []
    return [["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", UNIT_NAME]]


def _disable(plat: str) -> list[list[str]]:
    if plat == "darwin":
        return [["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]]
    if plat == "win32":
        return []
    return [["systemctl", "--user", "disable", "--now", UNIT_NAME],
            ["systemctl", "--user", "daemon-reload"]]


def _run(commands: list[list[str]], runner) -> list[tuple[list[str], int]]:
    """挨个跑，返回每条命令和它的退出码。

    一条失败不拦下一条：`launchctl bootout` 在没装过时本来就返回非 0，那不是错误。
    命令不存在（没装 systemd 的容器）同样只记一笔，让调用方去说人话。
    """
    out: list[tuple[list[str], int]] = []
    for command in commands:
        try:
            done = runner(command, capture_output=True)
            code = done.returncode
        except (OSError, subprocess.SubprocessError):
            code = 127
        out.append((command, code))
    return out


def install(home: pathlib.Path, plat: str, exe: str, socket: str, log: str,
            runner=subprocess.run) -> tuple[pathlib.Path, list[tuple[list[str], int]]]:
    """写服务描述文件并启用。返回落点和每条命令的退出码。"""
    path = unit_path(home, plat)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(plat, exe, socket, log), encoding="utf-8")
    if plat == "win32":
        return path, []
    return path, _run(_enable(plat, path), runner)


def uninstall(home: pathlib.Path, plat: str,
              runner=subprocess.run) -> tuple[pathlib.Path | None, list[tuple[list[str], int]]]:
    """停掉并删掉服务。本来就没装的话返回 `(None, [])`，不报错。"""
    path = unit_path(home, plat)
    if not path.exists():
        return None, []
    results = _run(_disable(plat), runner)
    path.unlink()
    return path, results


def status(home: pathlib.Path, plat: str) -> dict:
    """服务装没装。跑没跑是另一回事——那个看 bridge 自己的 socket，别问服务管理器。"""
    path = unit_path(home, plat)
    return {"installed": path.is_file(), "path": str(path), "platform": plat}


__all__ = ["LABEL", "UNIT_NAME", "install", "status", "uninstall", "unit_path", "unit_text"]
