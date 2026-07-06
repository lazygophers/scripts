"""系统级工具（防休眠）。"""
from __future__ import annotations

import subprocess

from .ui import reporter


def _start_caffeinate(args: list[str]):
    """启动 caffeinate, 失败抛 FileNotFoundError / SubprocessError。"""
    return subprocess.Popen(args)


def prevent_sleep(*, duration: int | None = None, command: list[str] | None = None) -> int:
    """防止 macOS 休眠。

    - command 非空: 跟随命令运行, 命令结束/退出码透传
    - duration 非 None: 持续 duration 秒
    - 否则: 无限制, 直到 Ctrl+C
    """
    r = reporter(stderr=True)
    r.rule("防止系统休眠")

    if command:
        return _prevent_sleep_for_command(command, r)
    if duration is not None:
        return _prevent_sleep_for_duration(duration, r)
    return _prevent_sleep_forever(r)


def _prevent_sleep_for_command(command: list[str], r) -> int:
    r.step(f"将跟随命令运行: {' '.join(command)}")
    try:
        proc = subprocess.Popen(command)
    except FileNotFoundError as e:
        r.err(f"命令不存在: {command[0]}")
        r.info(f"错误详情: {e}")
        return 127
    except subprocess.SubprocessError as e:
        r.err(f"启动命令失败: {' '.join(command)}")
        r.info(f"错误详情: {e}")
        return 1

    r.info(f"启动防休眠守护 (PID={proc.pid})...")
    caffeinate_proc = None
    try:
        try:
            caffeinate_proc = _start_caffeinate(["caffeinate", "-w", str(proc.pid)])
        except FileNotFoundError:
            r.err("caffeinate 命令不存在，此工具仅适用于 macOS")
            proc.terminate()
            proc.wait()
            return 1
        except subprocess.SubprocessError as e:
            r.err("启动 caffeinate 失败")
            r.info(f"错误详情: {e}")
            proc.terminate()
            proc.wait()
            return 1

        if caffeinate_proc.poll() is not None:
            r.err(f"caffeinate 进程启动失败 (exit={caffeinate_proc.returncode})")
            proc.terminate()
            proc.wait()
            return 1

        r.info("系统已进入防休眠模式")
        returncode = proc.wait()
        caffeinate_proc.terminate()
        caffeinate_proc.wait(timeout=5)

        if returncode == 0:
            r.ok("命令执行完成，防休眠结束 ✓")
            return 0
        r.warn(f"命令执行失败 (exit={returncode})，防休眠已结束")
        return returncode

    except KeyboardInterrupt:
        r.info("收到中断信号")
        proc.terminate()
        proc.wait()
        if caffeinate_proc is not None:
            caffeinate_proc.terminate()
            caffeinate_proc.wait(timeout=5)
        return 130


def _prevent_sleep_for_duration(duration: int, r) -> int:
    hours = duration / 3600
    r.info(f"防休眠时长: {duration} 秒 ({hours:.2f} 小时)")
    r.step("启动防休眠守护...")
    try:
        r.info(f"系统已进入防休眠模式，将持续 {duration} 秒")
        r.step("按 Ctrl+C 可提前结束")
        try:
            proc = _start_caffeinate(["caffeinate", "-t", str(duration)])
        except FileNotFoundError:
            r.err("caffeinate 命令不存在，此工具仅适用于 macOS")
            return 1
        except subprocess.SubprocessError as e:
            r.err("启动 caffeinate 失败")
            r.info(f"错误详情: {e}")
            return 1
        if proc.poll() is not None:
            r.err(f"caffeinate 进程启动失败 (exit={proc.returncode})")
            return 1
        returncode = proc.wait()
        if returncode == 0:
            r.ok(f"防休眠结束 (持续 {duration} 秒) ✓")
            return 0
        r.err(f"防休眠异常终止 (exit={returncode})")
        return 1
    except KeyboardInterrupt:
        r.info("收到中断信号，提前结束防休眠")
        return 0


def _prevent_sleep_forever(r) -> int:
    r.info("防休眠模式: 无限制")
    r.step("启动防休眠守护...")
    r.info("系统已进入防休眠模式，将持续运行直到手动停止")
    r.step("按 Ctrl+C 可结束防休眠")
    try:
        try:
            proc = _start_caffeinate(["caffeinate"])
        except FileNotFoundError:
            r.err("caffeinate 命令不存在，此工具仅适用于 macOS")
            return 1
        except subprocess.SubprocessError as e:
            r.err("启动 caffeinate 失败")
            r.info(f"错误详情: {e}")
            return 1
        if proc.poll() is not None:
            r.err(f"caffeinate 进程启动失败 (exit={proc.returncode})")
            return 1
        proc.wait()
        return 0
    except KeyboardInterrupt:
        r.info("收到中断信号，结束防休眠")
        return 0
