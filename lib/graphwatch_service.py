"""graphwatch 服务管理：三平台各一个 adapter，顶层只分派一次。

从 graphwatch.py 抽出的深 module（deletion test：删掉即浓缩）：install /
control / registered / state 四个动作是接口，launchd / systemd / schtasks 的
差异全部内化为各 adapter 的实现。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LAUNCHD_LABEL = "com.lazygophers.graphwatch"


def script_path() -> Path:
    """本仓 bin/graphwatch 绝对路径——服务里就跑它。"""
    return Path(__file__).resolve().parent.parent / "bin" / "graphwatch"


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.lazygophers.graphwatch.plist"


def launchd_plist() -> str:
    """LaunchAgent plist：KeepAlive 崩了自动拉起，RunAtLoad 登录自启。"""
    from lib.graphwatch import log_path

    exe = script_path()
    log = log_path()
    label = LAUNCHD_LABEL
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{exe}</string>
    <string>run</string>
  </array>
  <key>KeepAlive</key>
  <dict>
    <key>Crashed</key><true/>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def systemd_unit() -> str:
    """systemd --user 单元：Restart=always，登录自启（需 loginctl enable-linger 常驻）。"""
    exe = script_path()
    return f"""[Unit]
Description=graphwatch — graphify 全局 watch 守护
After=network.target

[Service]
ExecStart={sys.executable} {exe} run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def schtasks_create_command() -> list[str]:
    """Windows 登录自启计划任务命令（schtasks 用户级，无需管理员）。"""
    exe = script_path()
    return [
        "schtasks", "/Create", "/F",
        "/TN", "graphwatch",
        "/SC", "ONLOGON",
        "/TR", f'"{sys.executable}" "{exe}" run',
    ]


def _checked_runner():
    """默认服务命令执行器：默认 check=True（失败点抛错，不吞），调用方可覆盖。"""
    import subprocess

    def runner(cmd, **kw):
        kw.setdefault("check", True)
        return subprocess.run(cmd, capture_output=True, **kw)
    return runner


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _sh(*cmd: str) -> list[str]:
    """命令包一层 zsh -c。launchctl 直接作为 python 子进程跑时 bootout/bootstrap
    稳定报 'I/O error 5'（macOS 对 python 父进程的 XPC 判定），隔层 shell 即正常。"""
    import shlex

    return ["/bin/zsh", "-c", " ".join(shlex.quote(c) for c in cmd)]


def _launchd_gone(runner) -> bool:
    r = runner(_sh("launchctl", "print", f"{_launchd_domain()}/{LAUNCHD_LABEL}"), check=False)
    return getattr(r, "returncode", 1) != 0


def _launchd_stop(runner) -> None:
    """停掉服务并从 domain 卸载：kill SIGTERM（daemon 优雅退出）→ 等消失
    → bootout。直接 bootout 会在 daemon 退出期间报 'I/O error 5'；
    plist 的 KeepAlive 只在崩溃时拉起，正常退出不复活，kill 后即静止。"""
    domain = _launchd_domain()
    runner(_sh("launchctl", "kill", "SIGTERM", f"{domain}/{LAUNCHD_LABEL}"), check=False)
    for _ in range(10):
        if _launchd_gone(runner):
            return
        time.sleep(0.5)
    runner(_sh("launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"), check=False)
    for _ in range(10):
        if _launchd_gone(runner):
            return
        time.sleep(0.5)


def _launchd_start(runner, plist: str) -> None:
    """bootstrap（带重试防 launchd 清理竞态）。"""
    domain = _launchd_domain()
    last = None
    for _ in range(3):
        try:
            runner(_sh("launchctl", "bootstrap", domain, plist))
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1)
    raise last


# ── 平台 adapter：同一组动作，各自实现 ────────────────────────

class LaunchdService:
    """macOS launchd（现代 bootstrap/bootout API，旧 load/unload 会 rc 5）。"""

    def install(self, runner) -> None:
        p = launchd_plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(launchd_plist(), encoding="utf-8")
        _launchd_stop(runner)
        _launchd_start(runner, str(p))

    def uninstall(self, runner) -> None:
        p = launchd_plist_path()
        _launchd_stop(runner)
        if p.exists():
            p.unlink()

    def control(self, action: str, runner) -> None:
        p = str(launchd_plist_path())
        if action in ("stop", "restart"):
            _launchd_stop(runner)
        if action in ("start", "restart"):
            _launchd_start(runner, p)

    def registered(self, runner) -> bool:
        # 注册态 = plist 文件存在（stop 会 bootout 出 domain，但注册保留、
        # start 可重新 bootstrap）
        return launchd_plist_path().is_file()

    def state(self, runner, state: dict) -> dict:
        if not _launchd_gone(runner):
            import re

            r = runner(_sh("launchctl", "print", f"{_launchd_domain()}/{LAUNCHD_LABEL}"), check=False)
            text = getattr(r, "stdout", b"").decode(errors="replace") if isinstance(getattr(r, "stdout", None), bytes) else str(getattr(r, "stdout", "") or "")
            pid = re.search(r"^\s*pid = (\d+)", text, re.M)
            state["running"] = pid is not None
            state["pid"] = pid.group(1) if pid else "-"
            lex = re.search(r"^\s*last exit code = (.+)$", text, re.M)
            state["last_exit"] = lex.group(1).strip() if lex else "-"
        return state


class SystemdService:
    """Linux systemd --user（is-enabled 即磁盘注册态，无 launchd 的 domain 问题）。"""

    def install(self, runner) -> None:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "graphwatch.service").write_text(systemd_unit(), encoding="utf-8")
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", "--now", "graphwatch.service"])

    def uninstall(self, runner) -> None:
        runner(["systemctl", "--user", "disable", "--now", "graphwatch.service"])
        (Path.home() / ".config" / "systemd" / "user" / "graphwatch.service").unlink(missing_ok=True)

    def control(self, action: str, runner) -> None:
        runner(["systemctl", "--user", action, "graphwatch.service"])

    def registered(self, runner) -> bool:
        r = runner(["systemctl", "--user", "is-enabled", "graphwatch.service"])
        return r.returncode == 0

    def state(self, runner, state: dict) -> dict:
        r = runner(["systemctl", "--user", "show", "graphwatch.service",
                    "--property=MainPID,ActiveState,ExecMainStatus"])
        text = getattr(r, "stdout", b"").decode(errors="replace")
        for line in text.splitlines():
            if line.startswith("MainPID="):
                state["pid"] = line.split("=", 1)[1] or "-"
            elif line.startswith("ActiveState="):
                state["running"] = line.split("=", 1)[1] == "active"
            elif line.startswith("ExecMainStatus="):
                state["last_exit"] = line.split("=", 1)[1]
        return state


class SchtasksService:
    """Windows 计划任务（用户级 ONLOGON；Query 本身即磁盘注册态）。"""

    def install(self, runner) -> None:
        runner(schtasks_create_command())

    def uninstall(self, runner) -> None:
        runner(["schtasks", "/Delete", "/F", "/TN", "graphwatch"])

    def control(self, action: str, runner) -> None:
        if action in ("stop", "restart"):
            runner(["schtasks", "/End", "/TN", "graphwatch"], check=False)
        if action in ("start", "restart"):
            runner(["schtasks", "/Run", "/TN", "graphwatch"])

    def registered(self, runner) -> bool:
        r = runner(["schtasks", "/Query", "/TN", "graphwatch"])
        return r.returncode == 0

    def state(self, runner, state: dict) -> dict:
        from lib.graphwatch import daemon_alive

        state["running"] = daemon_alive()
        return state


def backend():
    """平台分派只此一处（原来是 5 个函数各自 switch）。"""
    plat = sys.platform
    if plat == "darwin":
        return LaunchdService()
    if plat.startswith("linux"):
        return SystemdService()
    return SchtasksService()


# ── 门面：graphwatch CLI 与测试打这里 ────────────────────────

def install_service(runner=None) -> None:
    """注册为用户级服务并立即启动。按平台走 launchd / systemd / schtasks。"""
    if runner is None:
        runner = _checked_runner()
    backend().install(runner)


def service_control(action: str, runner=None) -> None:
    """start / stop / restart 已注册的服务。stop 不动注册。"""
    from lib.graphwatch import GraphwatchError

    if runner is None:
        runner = _checked_runner()
    if action not in ("start", "stop", "restart"):
        raise GraphwatchError(f"未知动作 {action!r}，可用: start / stop / restart")
    if not service_registered():
        raise GraphwatchError("服务未注册，先: graphwatch install")
    backend().control(action, runner)


def uninstall_service(runner=None) -> None:
    """停止并删除服务注册。配置与日志不动。"""
    if runner is None:
        runner = _checked_runner()
    backend().uninstall(runner)


def service_registered(runner=None) -> bool:
    """服务注册态探测（磁盘态：plist / is-enabled / schtasks Query）。"""
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=False, capture_output=True, **kw)
    return backend().registered(runner)


def service_state(runner=None) -> dict:
    """服务执行态：注册 / 运行 / PID / 上次退出码 / 运行时长。"""
    import subprocess

    state = {"registered": service_registered(runner), "running": False,
             "pid": "-", "last_exit": "-", "uptime": "-"}
    if not state["registered"]:
        return state
    if runner is None:
        def runner(cmd, **kw):
            kw.setdefault("check", False)
            return subprocess.run(cmd, capture_output=True, **kw)
    return backend().state(runner, state)
