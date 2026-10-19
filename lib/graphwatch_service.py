"""graphwatch 服务管理：三平台各一个 adapter，顶层只分派一次。

从 graphwatch.py 抽出的深 module（deletion test：删掉即浓缩）：install /
control / registered / state 四个动作是接口，launchd / systemd / schtasks 的
差异全部内化为各 adapter 的实现。
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from lib.graphwatch_config import GraphwatchError


@dataclass(frozen=True)
class ServiceSpec:
    """一个守护服务的身份：服务名即 bin/<name>，launchd label、单元名都由它派生。"""

    name: str
    description: str
    env: tuple[tuple[str, str], ...] = ()

    @property
    def label(self) -> str:
        return f"com.lazygophers.{self.name}"


GRAPHWATCH = ServiceSpec("graphwatch", "graphwatch — graphify 全局 watch 守护",
                         (("GRAPHIFY_VIZ_NODE_LIMIT", "50000"),))
LAUNCHD_LABEL = GRAPHWATCH.label


def script_path(spec: ServiceSpec = GRAPHWATCH) -> Path:
    """本仓 bin/<name> 绝对路径——服务里就跑它。"""
    return Path(__file__).resolve().parent.parent / "bin" / spec.name


def launchd_plist_path(spec: ServiceSpec = GRAPHWATCH) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{spec.label}.plist"


def launchd_plist(spec: ServiceSpec = GRAPHWATCH) -> str:
    """LaunchAgent plist：KeepAlive 崩了自动拉起，RunAtLoad 登录自启。

    stdout/stderr 不重定向：Python 日志统一写 lib/log.py 的单一 JSONL 文件。
    """
    exe = script_path(spec)
    label = spec.label
    env = "".join(f"\n    <key>{k}</key><string>{v}</string>" for k, v in spec.env)
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
  <key>EnvironmentVariables</key>
  <dict>{env}
  </dict>
  <key>KeepAlive</key>
  <dict>
    <key>Crashed</key><true/>
  </dict>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
"""


def systemd_unit(spec: ServiceSpec = GRAPHWATCH) -> str:
    """systemd --user 单元：Restart=always，登录自启（需 loginctl enable-linger 常驻）。"""
    exe = script_path(spec)
    env = "".join(f"Environment={k}={v}\n" for k, v in spec.env)
    return f"""[Unit]
Description={spec.description}
After=network.target

[Service]
{env}ExecStart={sys.executable} {exe} run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def schtasks_create_command(spec: ServiceSpec = GRAPHWATCH) -> list[str]:
    """Windows 登录自启计划任务命令（schtasks 用户级，无需管理员）。"""
    exe = script_path(spec)
    return [
        "schtasks", "/Create", "/F",
        "/TN", spec.name,
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


def _launchd_gone(runner, label: str = LAUNCHD_LABEL) -> bool:
    r = runner(_sh("launchctl", "print", f"{_launchd_domain()}/{label}"), check=False)
    return getattr(r, "returncode", 1) != 0


def _launchd_stop(runner, label: str = LAUNCHD_LABEL) -> None:
    """停掉服务并从 domain 卸载：kill SIGTERM（daemon 优雅退出）→ 等消失
    → bootout。直接 bootout 会在 daemon 退出期间报 'I/O error 5'；
    plist 的 KeepAlive 只在崩溃时拉起，正常退出不复活，kill 后即静止。"""
    domain = _launchd_domain()
    runner(_sh("launchctl", "kill", "SIGTERM", f"{domain}/{label}"), check=False)
    for _ in range(10):
        if _launchd_gone(runner, label):
            return
        time.sleep(0.5)
    runner(_sh("launchctl", "bootout", f"{domain}/{label}"), check=False)
    for _ in range(10):
        if _launchd_gone(runner, label):
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

class _Adapter:
    def __init__(self, spec: ServiceSpec = GRAPHWATCH):
        self.spec = spec


class LaunchdService(_Adapter):
    """macOS launchd（现代 bootstrap/bootout API，旧 load/unload 会 rc 5）。"""

    def install(self, runner) -> None:
        p = launchd_plist_path(self.spec)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(launchd_plist(self.spec), encoding="utf-8")
        _launchd_stop(runner, self.spec.label)
        _launchd_start(runner, str(p))

    def uninstall(self, runner) -> None:
        p = launchd_plist_path(self.spec)
        _launchd_stop(runner, self.spec.label)
        if p.exists():
            p.unlink()

    def control(self, action: str, runner) -> None:
        p = str(launchd_plist_path(self.spec))
        if action in ("stop", "restart"):
            _launchd_stop(runner, self.spec.label)
        if action in ("start", "restart"):
            _launchd_start(runner, p)

    def registered(self, runner) -> bool:
        # 注册态 = plist 文件存在（stop 会 bootout 出 domain，但注册保留、
        # start 可重新 bootstrap）
        return launchd_plist_path(self.spec).is_file()

    def state(self, runner, state: dict, alive=None) -> dict:
        if not _launchd_gone(runner, self.spec.label):
            import re

            r = runner(_sh("launchctl", "print", f"{_launchd_domain()}/{self.spec.label}"), check=False)
            text = getattr(r, "stdout", b"").decode(errors="replace") if isinstance(getattr(r, "stdout", None), bytes) else str(getattr(r, "stdout", "") or "")
            pid = re.search(r"^\s*pid = (\d+)", text, re.M)
            state["running"] = pid is not None
            state["pid"] = pid.group(1) if pid else "-"
            lex = re.search(r"^\s*last exit code = (.+)$", text, re.M)
            state["last_exit"] = lex.group(1).strip() if lex else "-"
        return state


class SystemdService(_Adapter):
    """Linux systemd --user（is-enabled 即磁盘注册态，无 launchd 的 domain 问题）。"""

    @property
    def unit(self) -> str:
        return f"{self.spec.name}.service"

    def install(self, runner) -> None:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / self.unit).write_text(systemd_unit(self.spec), encoding="utf-8")
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", "--now", self.unit])

    def uninstall(self, runner) -> None:
        runner(["systemctl", "--user", "disable", "--now", self.unit])
        (Path.home() / ".config" / "systemd" / "user" / self.unit).unlink(missing_ok=True)

    def control(self, action: str, runner) -> None:
        runner(["systemctl", "--user", action, self.unit])

    def registered(self, runner) -> bool:
        r = runner(["systemctl", "--user", "is-enabled", self.unit])
        return r.returncode == 0

    def state(self, runner, state: dict, alive=None) -> dict:
        r = runner(["systemctl", "--user", "show", self.unit,
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


class SchtasksService(_Adapter):
    """Windows 计划任务（用户级 ONLOGON；Query 本身即磁盘注册态）。"""

    def install(self, runner) -> None:
        runner(schtasks_create_command(self.spec))

    def uninstall(self, runner) -> None:
        runner(["schtasks", "/Delete", "/F", "/TN", self.spec.name])

    def control(self, action: str, runner) -> None:
        if action in ("stop", "restart"):
            runner(["schtasks", "/End", "/TN", self.spec.name], check=False)
        if action in ("start", "restart"):
            runner(["schtasks", "/Run", "/TN", self.spec.name])

    def registered(self, runner) -> bool:
        r = runner(["schtasks", "/Query", "/TN", self.spec.name])
        return r.returncode == 0

    def state(self, runner, state: dict, alive=None) -> dict:
        # daemon_alive 是运行态查询，属主是 daemon；测试注入替身，生产惰性导入。
        if alive is None:
            from lib.graphwatch_daemon import daemon_alive as alive
        state["running"] = alive()
        return state


def backend(spec: ServiceSpec = GRAPHWATCH):
    """平台分派只此一处（原来是 5 个函数各自 switch）。"""
    plat = sys.platform
    if plat == "darwin":
        return LaunchdService(spec)
    if plat.startswith("linux"):
        return SystemdService(spec)
    return SchtasksService(spec)


# ── 门面：graphwatch CLI 与测试打这里 ────────────────────────

def install_service(runner=None, spec: ServiceSpec = GRAPHWATCH) -> None:
    """注册为用户级服务并立即启动。按平台走 launchd / systemd / schtasks。"""
    if runner is None:
        runner = _checked_runner()
    backend(spec).install(runner)


def service_control(action: str, runner=None, spec: ServiceSpec = GRAPHWATCH) -> None:
    """start / stop / restart 已注册的服务。stop 不动注册。"""
    if runner is None:
        runner = _checked_runner()
    if action not in ("start", "stop", "restart"):
        raise GraphwatchError(f"未知动作 {action!r}，可用: start / stop / restart")
    if not service_registered(runner, spec):
        raise GraphwatchError(f"服务未注册，先: {spec.name} install")
    backend(spec).control(action, runner)


def uninstall_service(runner=None, spec: ServiceSpec = GRAPHWATCH) -> None:
    """停止并删除服务注册。配置与日志不动。"""
    if runner is None:
        runner = _checked_runner()
    backend(spec).uninstall(runner)


def service_registered(runner=None, spec: ServiceSpec = GRAPHWATCH) -> bool:
    """服务注册态探测（磁盘态：plist / is-enabled / schtasks Query）。"""
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=False, capture_output=True, **kw)
    return backend(spec).registered(runner)


def service_state(runner=None, alive=None, spec: ServiceSpec = GRAPHWATCH) -> dict:
    """服务执行态：注册 / 运行 / PID / 上次退出码 / 运行时长。"""
    import subprocess

    state = {"registered": service_registered(runner, spec), "running": False,
             "pid": "-", "last_exit": "-", "uptime": "-"}
    if not state["registered"]:
        return state
    if runner is None:
        def runner(cmd, **kw):
            kw.setdefault("check", False)
            return subprocess.run(cmd, capture_output=True, **kw)
    return backend(spec).state(runner, state, alive)
