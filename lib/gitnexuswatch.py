"""gitnexuswatch — 让登记的 git 仓库的 gitnexus 代码图谱自动保鲜。

和 graphwatch 共用守护骨架（graphwatch_daemon.run_daemon 的排队/去重/热加载、
graphwatch_service 的三平台服务），这里只放 gitnexus 专属的四件事：
仓库发现（非 git 文件夹拆成里面的 git 仓库）、监听（全部仓库共用一个 watchdog
Observer）、重建（后台优先级跑 `gitnexus analyze --index-only`，等 git 操作结束）、
新鲜度（`.gitnexus/meta.json` 的 lastCommit 对 HEAD + 脏文件 mtime）。

只维护工作区索引（不带 --branch）：gitnexus 的 MCP 查询不带 branch 时只查它
（gitnexus README「Per-repo tools also take an optional branch … omitting it
queries the workspace index」）。切分支 = 工作区变了，同一份索引跟着重建。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from lib import log as _log
from lib.fire_base import BaseCli
from lib.graphwatch import _cmd
from lib.graphwatch_config import GraphwatchError, config_home, save_config
from lib.graphwatch_daemon import (
    FRESHNESS_COLOR,
    FRESHNESS_LABEL,
    STALE_EXCLUDED_DIRS,
    daemon_alive,
    run_daemon,
)
from lib.graphwatch_service import (
    ServiceSpec,
    install_service,
    script_path,
    service_control,
    service_state,
    uninstall_service,
)

NAME = "gitnexuswatch"
LOGGER = f"{NAME}-daemon"
DEFAULTS: dict = {"folders": [], "debounce": 30.0, "rebuild_concurrency": 1}
# 切分支 / 提交 / pull 后等这么久再建：git 写完 HEAD 还会写一阵工作区
HEAD_DEBOUNCE = 2.0
# 每次 HEAD 移动 git 都往 logs/HEAD 追加一行（commit/checkout/merge/reset/pull 全覆盖），
# HEAD 本身只在切分支时变；两个都盯
GIT_SIGNALS = frozenset({("HEAD",), ("logs", "HEAD")})
# 这些存在 = git 操作进行中，此刻建出来的图谱是半新半旧
GIT_BUSY = ("index.lock", "rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
GIT_WAIT_SECS = 60
ANALYZE_TIMEOUT = 3600


def config_path() -> Path:
    return config_home() / f"{NAME}.yaml"


def lock_path() -> Path:
    return config_home() / f"{NAME}.lock"


def load_config() -> dict:
    import yaml

    cfg = dict(DEFAULTS)
    p = config_path()
    if p.is_file():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise GraphwatchError(f"配置文件不是合法 YAML: {p}\n{e}") from e
        if not isinstance(data, dict):
            raise GraphwatchError(f"配置文件顶层应是映射: {p}")
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    if not isinstance(cfg["folders"], list) or not all(isinstance(f, str) for f in cfg["folders"]):
        raise GraphwatchError(f"folders 应是仓库路径列表: {p}")
    try:
        cfg["debounce"] = float(cfg["debounce"])
        cfg["rebuild_concurrency"] = int(cfg["rebuild_concurrency"])
    except (TypeError, ValueError) as e:
        raise GraphwatchError(f"debounce 应是数字、rebuild_concurrency 应是整数: {p}") from e
    if cfg["debounce"] <= 0 or cfg["rebuild_concurrency"] < 1:
        raise GraphwatchError(f"debounce 应大于 0，rebuild_concurrency 最小为 1: {p}")
    return cfg


# ── 仓库发现 ────────────────────────────────────────────────

def find_repos(root: Path) -> list[str]:
    """root 本身是 git 仓库就是它；否则往下找，找到仓库不再深入（子模块不单独登记）。

    跳过依赖/产物目录和隐藏目录（.claude/worktrees 下的 agent worktree 是别处代码的副本）。
    """
    root = root.expanduser().resolve()
    if (root / ".git").exists():
        return [str(root)]
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            found.append(dirpath)
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in STALE_EXCLUDED_DIRS and not d.startswith("."))
    return found


def add_folder(path: Path) -> list[str]:
    if not path.expanduser().is_dir():
        raise GraphwatchError(f"目录不存在: {path}")
    repos = find_repos(path)
    if not repos:
        raise GraphwatchError(f"{path} 下没有 git 仓库（gitnexus 只能给 git 仓库建索引）")
    cfg = load_config()
    added = [r for r in repos if r not in cfg["folders"]]
    if added:
        cfg["folders"] = [*cfg["folders"], *added]
        save_config(cfg, config_path())
    return repos


def remove_folder(path: Path) -> list[str]:
    """注销 path 本身及其下所有登记的仓库（`remove ~/bcpay` 一次去掉 12 个）。"""
    root = str(path.expanduser().resolve())
    cfg = load_config()
    gone = [f for f in cfg["folders"] if f == root or f.startswith(root.rstrip(os.sep) + os.sep)]
    if not gone:
        raise GraphwatchError(f"未登记过: {root}")
    cfg["folders"] = [f for f in cfg["folders"] if f not in gone]
    save_config(cfg, config_path())
    return gone


# ── git 状态 ────────────────────────────────────────────────

def git_dir(repo: str) -> Path:
    """仓库的 git 目录。worktree / 子模块的 .git 是文件，内容是 `gitdir: <路径>`。"""
    dot = Path(repo) / ".git"
    if dot.is_file():
        target = dot.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
        return (Path(repo) / target).resolve()
    return dot


def git_busy(gdir: Path) -> bool:
    return any((gdir / name).exists() for name in GIT_BUSY)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def stale(repo: str) -> Path | None:
    """索引落后于工作区时返回触发它的那个路径（daemon 补课按它的 mtime 排序），新鲜则 None。

    三种落后：从没建过（meta.json 不在）、HEAD 移动过（lastCommit ≠ HEAD）、
    有比上次索引新的未提交改动（git status 列出、mtime 晚于 meta.json）。
    """
    meta = Path(repo) / ".gitnexus" / "meta.json"
    if not meta.is_file():
        return meta
    try:
        indexed = json.loads(meta.read_text(encoding="utf-8")).get("lastCommit")
    except (OSError, ValueError):
        return meta
    try:
        if _git(repo, "rev-parse", "HEAD").strip() != indexed:
            return git_dir(repo) / "HEAD"
        porcelain = _git(repo, "status", "--porcelain", "-z")
    except subprocess.CalledProcessError:
        return None  # 空仓库（没有 HEAD）：没东西可建
    built = meta.stat().st_mtime
    fields = porcelain.split("\0")
    newest: tuple[float, Path] | None = None
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            i += 1  # 重命名/复制后面跟一个原路径字段
        p = Path(repo) / entry[3:]
        try:
            m = p.stat().st_mtime
        except OSError:
            m = time.time()  # 已删除的文件：删除本身就是改动
        if m > built and (newest is None or m > newest[0]):
            newest = (m, p)
    return newest[1] if newest else None


def freshness(repo: str, running: bool | None = None) -> tuple[str, str]:
    meta = Path(repo) / ".gitnexus" / "meta.json"
    if not (Path(repo) / ".git").exists():
        return "fail", "不是 git 仓库（目录被移走了？）"
    if not meta.is_file():
        return "skip", "无索引（尚未构建）"
    if stale(repo) is not None:
        if running is None:
            running = daemon_alive(lock_path())
        return ("updating", "更新中（daemon 排队重建）") if running else ("fail", "索引过期")
    import datetime

    ts = datetime.datetime.fromtimestamp(meta.stat().st_mtime).strftime("%m-%d %H:%M")
    return "ok", f"新鲜（索引于 {ts}）"


# ── 监听：所有仓库共用一个 Observer ─────────────────────────────

_observer = None
_observer_lock = threading.Lock()


def _shared_observer():
    """macOS 上一个 Observer 一条 FSEvents 流；共用一个，37 个仓库也只多一个线程。"""
    global _observer
    with _observer_lock:
        if _observer is None:
            from watchdog.observers import Observer

            _observer = Observer()
            _observer.daemon = True
            _observer.start()
        return _observer


def classify(repo_parts: tuple[str, ...], path: str) -> str | None:
    """事件归类：'git'（HEAD 移动，快建）/ 'file'（工作区改动，防抖）/ None（忽略）。"""
    rel = Path(path).parts[len(repo_parts):]
    if not rel:
        return None
    if rel[0] == ".git":
        return "git" if rel[1:] in GIT_SIGNALS else None
    if rel[0] == ".gitnexus" or STALE_EXCLUDED_DIRS & set(rel[:-1]):
        return None
    return "file"


class _Listener:
    """run_daemon 的 listener 接口（start / stop / join）；start 时挂到共用 Observer。"""

    def __init__(self, repo: str, debounce: float, on_change):
        from watchdog.events import FileSystemEventHandler

        parts = Path(repo).parts
        state = {"timer": None, "fast": False}
        guard = threading.Lock()

        def schedule(kind: str) -> None:
            with guard:
                fast = kind == "git"
                if state["timer"] is not None:
                    if state["fast"] and not fast:
                        return  # 已排了快建，工作区写入不把它往后推
                    state["timer"].cancel()
                t = threading.Timer(HEAD_DEBOUNCE if fast else debounce, on_change)
                t.daemon = True
                t.start()
                state["timer"], state["fast"] = t, fast

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:
                if event.is_directory:
                    return
                kinds = {classify(parts, event.src_path), classify(parts, getattr(event, "dest_path", "") or "")}
                if "git" in kinds:
                    schedule("git")
                elif "file" in kinds:
                    schedule("file")

        self._repo, self._handler, self._watch = repo, _Handler(), None

    def start(self) -> None:
        self._watch = _shared_observer().schedule(self._handler, self._repo, recursive=True)

    def stop(self) -> None:
        if self._watch is not None:
            _shared_observer().unschedule(self._watch)
            self._watch = None

    def join(self, timeout=None) -> None:
        pass


# ── 重建 ───────────────────────────────────────────────────

def gitnexus_bin() -> str:
    exe = shutil.which("gitnexus")
    if exe is None:
        raise GraphwatchError("找不到 gitnexus 命令。先装: npm i -g gitnexus")
    return exe


def low_priority() -> list[str]:
    """macOS 用后台 QoS（CPU + 磁盘 IO 都降级，前台不卡）；其它平台用 nice。"""
    return ["taskpolicy", "-b"] if sys.platform == "darwin" else ["nice", "-n", "19"]


def rebuild(repo: str, sleep=time.sleep, run=subprocess.run) -> int:
    """跑一次增量 analyze。git 操作进行中先等它结束，等不到就跳过——
    rebase/merge 收尾时 HEAD 会再动一次，那时自然再触发。"""
    gdir = git_dir(repo)
    for _ in range(GIT_WAIT_SECS):
        if not git_busy(gdir):
            break
        sleep(1)
    else:
        _log.record("gitnexuswatch.skip", logger=LOGGER, folder=repo, reason="git 操作进行中")
        return 0
    started = time.monotonic()
    r = run([*low_priority(), gitnexus_bin(), "analyze", "--index-only", repo],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=ANALYZE_TIMEOUT)
    fields = {"folder": repo, "rc": r.returncode, "secs": round(time.monotonic() - started, 1)}
    if r.returncode:
        fields["stderr"] = (r.stderr or "")[-2000:]
    _log.record("gitnexuswatch.rebuild", logger=LOGGER, level="error" if r.returncode else "info", **fields)
    return r.returncode


def ensure() -> None:
    try:
        import watchdog  # noqa: F401
    except ImportError as e:
        raise GraphwatchError("watchdog 未安装（监听依赖）: pip3 install watchdog") from e
    gitnexus_bin()


SPEC = ServiceSpec(NAME, "gitnexuswatch — gitnexus 代码图谱自动保鲜")


def service_spec() -> ServiceSpec:
    """安装用的完整 spec。launchd/systemd 的 PATH 不含 mise/nvm 的 node 目录；把 gitnexus 所在目录写进服务环境，
    gitnexus 的 `#!/usr/bin/env node` 才找得到同目录的 node。"""
    bindir = str(Path(gitnexus_bin()).parent)
    return ServiceSpec(SPEC.name, SPEC.description,
                       (("PATH", f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin"),))


def run(stop_event=None, **kw) -> None:
    run_daemon(stop_event=stop_event, ensure=ensure, rebuild_runner=rebuild, listener_factory=_Listener,
               name=NAME, load=load_config, lock_file=lock_path(), stale=stale, **kw)


# ── CLI ────────────────────────────────────────────────────

class GitnexuswatchCli(BaseCli):
    """gitnexuswatch CLI。子命令：add / remove / list / rebuild / run / install / start / stop / restart / uninstall / status。"""

    @_cmd
    def add(self, directory: str) -> int:
        """登记仓库；不是 git 仓库的文件夹会拆成里面的 git 仓库逐个登记。

        用法: gitnexuswatch add ~/bcpay
        """
        for repo in add_folder(Path(directory)):
            self._r.ok(f"已登记: {repo}")
        return 0

    @_cmd
    def remove(self, directory: str) -> int:
        """注销仓库（给文件夹则注销它下面的全部仓库）。

        用法: gitnexuswatch remove ~/bcpay
        """
        for repo in remove_folder(Path(directory)):
            self._r.ok(f"已注销: {repo}")
        return 0

    @_cmd
    def list(self) -> int:
        """列出已登记的仓库和索引新鲜度。

        用法: gitnexuswatch list
        """
        return self._table(load_config()["folders"], f"{NAME} 登记表")

    @_cmd
    def rebuild(self, directory: str = "") -> int:
        """前台立即重建一个仓库的索引（不进 daemon 队列、不等防抖）。

        用法: gitnexuswatch rebuild ~/code/x    # 缺省为当前目录
        """
        repo = str(Path(directory or ".").expanduser().resolve())
        if not (Path(repo) / ".git").exists():
            raise GraphwatchError(f"不是 git 仓库: {repo}")
        rc = rebuild(repo)
        if rc:
            self._r.err(f"重建失败（rc={rc}），stderr 已写入统一日志: {_log.path()}")
        return rc

    @_cmd
    def run(self) -> int:
        """前台运行守护进程（全局单例，Ctrl-C 退出）。

        用法: gitnexuswatch run
        """
        try:
            run()
        except KeyboardInterrupt:
            pass
        return 0

    @_cmd
    def install(self) -> int:
        """注册为用户级系统服务并立即启动（登录自启）。

        用法: gitnexuswatch install
        """
        ensure()
        install_service(spec=service_spec())
        self._r.ok(f"已注册并启动：{script_path(SPEC)} run")
        return 0

    @_cmd
    def start(self) -> int:
        """启动已注册的服务。用法: gitnexuswatch start"""
        service_control("start", spec=SPEC)
        self._r.ok("服务已启动")
        return 0

    @_cmd
    def stop(self) -> int:
        """停止服务（注册保留）。用法: gitnexuswatch stop"""
        service_control("stop", spec=SPEC)
        self._r.ok("服务已停止（注册保留，start 恢复）")
        return 0

    @_cmd
    def restart(self) -> int:
        """重启服务。用法: gitnexuswatch restart"""
        service_control("restart", spec=SPEC)
        self._r.ok("服务已重启")
        return 0

    @_cmd
    def uninstall(self) -> int:
        """停止并删除服务注册（配置与索引保留）。用法: gitnexuswatch uninstall"""
        uninstall_service(spec=SPEC)
        self._r.ok("服务已注销（配置与索引保留）")
        return 0

    @_cmd
    def status(self) -> int:
        """服务状态 + 各仓库索引新鲜度。用法: gitnexuswatch status"""
        st = service_state(spec=SPEC, alive=lambda: daemon_alive(lock_path()))
        if not st["registered"]:
            self._r.warn(f"服务未注册。先: {NAME} install")
        head = ("已注册" if st["registered"] else "未注册") + (f" · 运行中 PID {st['pid']}" if st["running"] else " · 没在跑")
        return self._table(load_config()["folders"], f"{NAME} 状态（服务{head}）")

    def _table(self, repos: list[str], title: str) -> int:
        if not repos:
            self._r.info(f"还没有登记任何仓库。先: {NAME} add <目录>")
            return 0
        running = daemon_alive(lock_path())
        rows = [(r, *freshness(r, running)) for r in repos]
        from lib.ai_env import is_ai_shell_env

        if is_ai_shell_env():
            for repo, st, detail in rows:
                print(f"{repo} | {FRESHNESS_LABEL[st]} | {detail}")
            return 0
        from rich.table import Table

        table = Table(title=f"{title}（{len(repos)} 个仓库）")
        table.add_column("仓库", style="bold")
        table.add_column("索引")
        for repo, st, detail in rows:
            color = FRESHNESS_COLOR[st]
            table.add_row(repo, f"[{color}]{detail}[/{color}]")
        self._r.console.print(table)
        return 0
