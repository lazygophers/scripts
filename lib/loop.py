"""循环执行命令并追踪结果。"""

from __future__ import annotations

from pathlib import Path

from lib.exec import run, shell_join
from lib.ui import reporter


def run_loop(
    cmd: list[str],
    *,
    count: int | None = None,
    force: bool = False,
    timeout: int | None = None,
    infinite: bool = False,
) -> int:
    """循环执行命令并追踪成功/失败。

    Args:
        cmd: 要执行的命令（已拆分为 argv list）
        count: 有限循环次数；None 表示无限
        force: True=强制跑满次数；False=首次成功即停
        timeout: 单次命令超时（秒）—— 当前 run 不支持, 仅占位
        infinite: 显式无限模式（覆盖 count）
    """
    r = reporter(stderr=True)
    cmd_str = shell_join(cmd)

    shell_special = {'|', '&', ';', '<', '>', '(', ')'}
    if any(c in cmd_str[:100] for c in shell_special) and 'sh -c' not in cmd_str:
        r.err("命令包含管道/条件操作符，但未使用 sh -c 包裹")
        r.info("正确用法: loop 100 sh -c 'make build && make test'")
        return 2

    is_infinite = infinite or count is None
    label_count = "∞" if is_infinite else str(count)

    r.rule("循环执行", style="blue")
    r.kv("任务", {
        "命令": cmd_str,
        "次数": label_count,
        "模式": "force" if force else "auto-stop",
    })

    success_count = 0
    failure_count = 0
    early_stop = False
    last_i = 0

    def _run_once(label: str) -> bool:
        """执行单次。返回是否成功。"""
        nonlocal success_count, failure_count
        r.step(label)
        result = run(cmd, check=False, capture_output=False)
        if result.returncode == 0:
            success_count += 1
            if not force:
                r.ok(f"{label} → 命令成功，自动停止")
            return True
        failure_count += 1
        r.err(f"{label} → 失败 (exit={result.returncode})")
        return False

    if is_infinite:
        i = 0
        while True:
            i += 1
            last_i = i
            ok = _run_once(f"[#{i}] {cmd_str}")
            if ok and not force:
                early_stop = True
                break
    else:
        for i in range(1, count + 1):
            last_i = i
            ok = _run_once(f"[{i}/{count}] {cmd_str}")
            if ok and not force:
                early_stop = True
                break

    r.rule("执行结果", style="green" if failure_count == 0 else "red")
    r.summary("", [
        ("总次数", str(last_i), None),
        ("成功", str(success_count), "green" if success_count > 0 else None),
        ("失败", str(failure_count), "red" if failure_count > 0 else None),
        ("状态", "提前停止" if early_stop else "全部完成",
         "yellow" if early_stop else "green"),
    ])
    return 0 if failure_count == 0 else 1
