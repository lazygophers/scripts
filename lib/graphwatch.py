"""graphwatch — CLI 门面与全量再导出。

结构：graphwatch_config（配置/注册表/向导）→ graphwatch_daemon（监听/重建/
锁/日志/通知/新鲜度）→ graphwatch_service（三平台服务管理）。本文件只保留
CLI 与向后兼容的再导出；新代码直接 import 对应模块。
"""

from __future__ import annotations

import os  # noqa: F401  （测试经 graphwatch.os / .sys / .time 打补丁）
import sys  # noqa: F401
import time  # noqa: F401
from pathlib import Path

from lib.fire_base import BaseCli, timed_cli
from lib.graphwatch_config import (  # noqa: F401
    BACKEND_CHOICES,
    GraphwatchError,
    add_folder,
    config_home,
    config_path,
    list_folders,
    load_config,
    mask_secret,
    remove_folder,
    run_wizard,
    save_config,
)
from lib.graphwatch_daemon import (  # noqa: F401
    FRESHNESS_COLOR,
    FRESHNESS_LABEL,
    LOG_BACKUPS,
    LOG_MAX_BYTES,
    NOTIFY_THROTTLE_SECS,
    STALE_EXCLUDED_DIRS,
    Notifier,
    _watched_extensions,
    acquire_singleton_lock,
    daemon_alive,
    ensure_graphify,
    folder_freshness,
    lock_path,
    log_path,
    notify,
    release_singleton_lock,
    rotate_log,
    run_daemon,
    stale_trigger,
    tail_log,
)
from lib.graphwatch_service import (  # noqa: F401
    install_service,
    script_path,
    service_control,
    service_registered,
    service_state,
    uninstall_service,
)


def _cmd(method):
    """子命令装饰器：计时 + GraphwatchError 转一行人话（同 archery 的 cmd）。"""

    @timed_cli
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except GraphwatchError as e:
            self._r.err(str(e))
            return 2

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class GraphwatchCli(BaseCli):
    """graphwatch CLI。子命令：add / remove / list / run / config / install / start / stop / restart / uninstall / status。"""

    @_cmd
    def add(self, directory: str) -> int:
        """注册一个文件夹，daemon 会自动监听（热加载）。

        用法: graphwatch add ~/code/some-repo
        """
        stored = add_folder(Path(directory))
        self._r.ok(f"已注册: {stored}")
        return 0

    @_cmd
    def remove(self, directory: str) -> int:
        """注销一个文件夹，daemon 自动停监（热加载）。

        用法: graphwatch remove ~/code/some-repo
        """
        stored = remove_folder(Path(directory))
        self._r.ok(f"已注销: {stored}")
        return 0

    @_cmd
    def list(self) -> int:
        """列出已注册的文件夹。

        用法: graphwatch list
        """
        folders = list_folders()
        if not folders:
            self._r.info("还没有注册任何文件夹。先: graphwatch add <目录>")
            return 0
        from rich.table import Table

        table = Table(title=f"graphwatch 注册表（{len(folders)} 个目录）")
        table.add_column("目录", style="bold")
        table.add_column("图谱")
        for f in folders:
            st, detail = folder_freshness(f)
            color = FRESHNESS_COLOR[st]
            table.add_row(f, f"[{color}]{detail}[/{color}]")
        self._r.console.print(table)
        return 0

    @_cmd
    def run(self) -> int:
        """前台运行守护进程（全局单例，Ctrl-C 退出）。

        用法: graphwatch run
        """
        try:
            run_daemon()
        except KeyboardInterrupt:
            pass
        return 0

    @_cmd
    def config(self, action: str = "wizard") -> int:
        """引导式配置向导（backend/api_key/base_url/model/debounce）。

        用法: graphwatch config          # 进向导
              graphwatch config show     # 脱敏查看当前生效配置
        """
        if action == "show":
            cfg = load_config()
            from rich.table import Table

            table = Table(title=f"graphwatch 配置（{config_path()}）")
            table.add_column("字段", style="bold")
            table.add_column("值")
            table.add_row("folders", f"{len(cfg['folders'])} 个目录（graphwatch list 查看）")
            table.add_row("backend", str(cfg["backend"]) or "（空）")
            table.add_row("api_key", mask_secret(str(cfg["api_key"])) or "（空）")
            table.add_row("base_url", str(cfg["base_url"]) or "（空）")
            table.add_row("model", str(cfg["model"]) or "（空）")
            table.add_row("debounce", str(cfg["debounce"]))
            self._r.console.print(table)
            return 0
        if action != "wizard":
            self._r.err(f"未知 config 动作 {action!r}，可用: wizard（默认）/ show")
            return 2
        run_wizard()
        return 0

    @_cmd
    def install(self) -> int:
        """注册为用户级系统服务并立即启动（登录自启，无需 root）。

        用法: graphwatch install
        """
        ensure_graphify()
        install_service()
        self._r.ok(f"已注册并启动：{script_path()} run")
        return 0

    @_cmd
    def start(self) -> int:
        """启动已注册的服务（等价于 launchctl load / systemctl start）。

        用法: graphwatch start
        """
        service_control("start")
        self._r.ok("服务已启动")
        return 0

    @_cmd
    def stop(self) -> int:
        """停止服务进程（注册保留，下次 start 或重启电脑恢复）。

        用法: graphwatch stop
        """
        service_control("stop")
        self._r.ok("服务已停止（注册保留，start 恢复）")
        return 0

    @_cmd
    def restart(self) -> int:
        """重启服务（重读配置，热加载之外的全量刷新）。

        用法: graphwatch restart
        """
        service_control("restart")
        self._r.ok("服务已重启")
        return 0

    @_cmd
    def uninstall(self) -> int:
        """停止并删除服务注册（配置与日志保留）。

        用法: graphwatch uninstall
        """
        uninstall_service()
        self._r.ok("服务已注销（配置与日志保留）")
        return 0

    @_cmd
    def status(self, log: int = 10) -> int:
        """查看服务执行状态、各目录图谱新鲜度、最近日志。

        用法: graphwatch status            # 服务态 + 新鲜度 + 最近 10 行日志
              graphwatch status --log 50   # 多看些日志；--log 0 关闭日志段
        """
        from rich.table import Table

        rows: list[tuple] = []
        st = service_state()
        head = "已注册" if st["registered"] else "未注册"
        head += f" · {'运行中' if st['running'] else '没在跑'}"
        head += f" · PID {st['pid']}" if st["running"] else ""
        head += f" · 上次退出码 {st['last_exit']}" if st["registered"] else ""
        if not st["registered"]:
            self._r.warn("服务未注册。先: graphwatch install")
        for f in list_folders():
            st, detail = folder_freshness(f)
            rows.append((f, st, detail))
        table = Table(title=f"graphwatch 状态（服务{head}）")
        table.add_column("目录", style="bold")
        table.add_column("图谱")
        table.add_column("详情")
        for folder, fs, detail in rows:
            color = FRESHNESS_COLOR[fs]
            label = FRESHNESS_LABEL[fs]
            table.add_row(folder, f"[{color}]{label}[/{color}]", detail)
        self._r.console.print(table)
        if log:
            lines = tail_log(log)
            self._r.console.print(f"[bold blue]最近日志[/bold blue] [dim]({log_path()})[/dim]")
            if not lines:
                self._r.console.print("  [dim]（暂无）[/dim]")
            for line in lines:
                self._r.console.print(f"  [dim]{line}[/dim]")
        return 0
