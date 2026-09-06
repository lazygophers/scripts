"""graphwatch daemon：监听 + 重建队列 + 单例锁 + 日志 + 通知 + 新鲜度。

依赖 graphwatch_config（配置/注册表），不知道 service 的存在。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from lib.graphwatch_config import GraphwatchError, config_home, load_config

# 新鲜度比对时整目录排除：构建产物/依赖目录不是「源码改动」，不排除的话
# IDE/构建器一碰 build/ 就永远显示过期
STALE_EXCLUDED_DIRS = frozenset({
    "node_modules", "target", "build", "dist", "out", "obj",
    ".venv", "venv", "__pycache__", ".dart_tool", "Pods", "DerivedData",
    ".next", ".nuxt", ".cache", ".idea", ".gradle", ".terraform",
})


# 图谱新鲜度状态 → (列标签, 色)；list / status 共用
FRESHNESS_LABEL: dict[str, str] = {"ok": "新鲜", "skip": "未构建", "fail": "过期", "updating": "更新中"}
FRESHNESS_COLOR: dict[str, str] = {"ok": "green", "skip": "yellow", "fail": "red", "updating": "cyan"}




def ensure_graphify() -> None:
    """graphify 库可用性探测：缺席时给安装指引而不是神秘 traceback。

    watchdog 是 `graphify watch` 子进程的硬依赖（缺了它监听进程启动即死、
    只在日志里留一行 error），所以在这里一起探。
    """
    try:
        import graphify  # noqa: F401
    except ImportError as e:
        raise GraphwatchError(
            "graphify 库未安装。graphwatch 只用它、不自己解析代码，请装上：\n"
            "  pip3 install -r requirements.txt"
        ) from e
    try:
        import watchdog  # noqa: F401
    except ImportError as e:
        raise GraphwatchError(
            "watchdog 未安装（graphify watch 的监听依赖，缺了子进程起不来）：\n"
            "  pip3 install -r requirements.txt"
        ) from e



def lock_path() -> Path:
    return config_home() / "graphwatch.lock"


def acquire_singleton_lock():
    """拿全局单例锁（非阻塞）。成功返回 fd，已被占返回 None。

    锁内容写 PID，方便排障；进程退出 fd 自动关闭、锁自动释放。
    """
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.truncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    os.fsync(fd)
    return fd


def release_singleton_lock(fd) -> None:
    """释放单例锁并关闭 fd。"""
    if fd is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
NOTIFY_THROTTLE_SECS = 300


def log_path() -> Path:
    return config_home() / "logs" / "graphwatch.log"


def rotate_log() -> None:
    """按大小轮转 daemon 日志：graphwatch.log → .1 → … → .3。

    ponytail: 轮转后仍存活的监听子进程 fd 指向旧 inode，继续写进 .1，
    直到该目录重启才切回新文件；watch 日志量小，可接受。
    """
    p = log_path()
    if not p.is_file() or p.stat().st_size < LOG_MAX_BYTES:
        return
    for i in range(LOG_BACKUPS - 1, 0, -1):
        src = p.with_suffix(f".log.{i}")
        if src.exists():
            src.replace(p.with_suffix(f".log.{i + 1}"))
    p.replace(p.with_suffix(".log.1"))
    p.touch()


def notify(title: str, message: str, runner=None) -> bool:
    """系统弹窗通知（无语音）。失败返回 False，不抛——通知是尽力而为。

    每次调用（无论成败）都先在日志文件留一行：弹窗是给用户看的，
    日志是给排障看的，两者必须在同一处可对上。
    """
    import subprocess

    def _audit(ok: bool) -> None:
        try:
            log_path().parent.mkdir(parents=True, exist_ok=True)
            import datetime

            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_path().open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] 通知({'已发' if ok else '失败'}): {title} — {message.splitlines()[0]}\n")
        except OSError:
            pass  # 审计失败不影响通知本身

    if runner is None:
        runner = subprocess.run
    # 各平台把双引号换成单引号：osascript 双引号串 / PowerShell 单引号串都吃不消嵌套引号
    quoted = message.replace('"', "'")
    tquoted = title.replace('"', "'")
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", f'display notification "{quoted}" with title "{tquoted}"']
    elif sys.platform.startswith("linux"):
        cmd = ["notify-send", title, message]
    else:
        # PowerShell 单引号串里 ' 要双写转义，防消息含引号截断
        psq = quoted.replace("'", "''")
        pst = tquoted.replace("'", "''")
        # win32：PowerShell 气泡通知，无需额外安装
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Warning;"
            f"$n.Visible = $true; $n.ShowBalloonTip(5000, '{pst}', '{psq}', 'Warning');"
            "Start-Sleep -Seconds 6; $n.Dispose()"
        )
        cmd = ["powershell", "-NoProfile", "-Command", ps]
    try:
        ok = runner(cmd, check=False, capture_output=True, timeout=15).returncode == 0
    except Exception:
        ok = False
    _audit(ok)
    return ok


class Notifier:
    """带节流的失败通知：同目录 throttle_secs 内只发一次，成功不通知。"""

    def __init__(self, throttle_secs: float = NOTIFY_THROTTLE_SECS, sender=None):
        self._throttle = throttle_secs
        self._last: dict[str, float] = {}
        self._sender = sender or (lambda folder, title, msg: notify(title, msg))

    def fire(self, folder: str, title: str, message: str) -> bool:
        now = time.monotonic()
        last = self._last.get(folder)
        if last is not None and now - last < self._throttle:
            return False
        self._last[folder] = now
        return bool(self._sender(folder, title, message))


def _watched_extensions() -> frozenset[str]:
    """graphify watch 监听的扩展名（变更才触发重建）；导入失败回落常用集。"""
    try:
        from graphify.detect import CODE_EXTENSIONS, DOC_EXTENSIONS, IMAGE_EXTENSIONS, PAPER_EXTENSIONS

        return frozenset(CODE_EXTENSIONS | DOC_EXTENSIONS | PAPER_EXTENSIONS | IMAGE_EXTENSIONS)
    except Exception:
        # ponytail: 回落集只保最常见的源/文档后缀，graphify 缺席时够用
        return frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb",
                          ".c", ".h", ".cpp", ".hpp", ".md", ".txt", ".yaml", ".yml", ".json"})


def _watchdog_listener(folder: str, debounce: float, on_change) -> object:
    """单目录监听器：watchdog 观察文件事件，防抖后回调 on_change。

    自己写而不是跑 `graphify watch` 子进程：监听与重建必须拆开，重建才能
    全局排队（并发可配，默认串行）。
    """
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    watched = _watched_extensions()

    class _Handler(FileSystemEventHandler):
        def __init__(self):
            self._timer = None

        def _schedule(self):
            if self._timer is not None:
                self._timer.cancel()
            import threading

            t = threading.Timer(debounce, on_change)
            t.daemon = True
            t.start()
            self._timer = t

        def on_any_event(self, event) -> None:
            from pathlib import Path as _P

            if event.is_directory:
                return
            p = _P(event.src_path)
            if "graphify-out" in p.parts or ".git" in p.parts:
                return
            if p.suffix.lower() not in watched:
                return
            self._schedule()

    obs = Observer()
    obs.schedule(_Handler(), folder, recursive=True)
    obs.daemon = True
    return obs


def _run_update(folder: str) -> int:
    """跑一次增量重建（graphify update，无 LLM）。返回退出码。"""
    import subprocess

    r = subprocess.run(
        [sys.executable, "-m", "graphify", "update", folder],
        stdout=getattr(run_daemon, "_log_file", None) or sys.stderr,
        stderr=getattr(run_daemon, "_log_file", None) or sys.stderr,
        check=False,
    )
    return r.returncode


def run_daemon(stop_event=None, ensure=None, rebuild_runner=None, listener_factory=None,
               poll_interval: float = 2.0, on_started=None, notifier=None) -> None:
    """前台守护进程：每目录一个监听线程，重建全局排队，全局单例锁防双开。

    监听（watchdog，轻量）与重建（graphify update 子进程，重）分离：
    重建进全局队列，由 rebuild_concurrency 个 worker 执行（默认 1 = 串行，
    最小 1）。同一目录的变更在队列里去重合并，重建中再变更则重跑一轮。
    主循环每 poll_interval 醒一次热加载配置；stop_event 停一切。
    """
    import queue
    import threading

    if stop_event is None:
        stop_event = threading.Event()
    try:
        import signal

        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    except ValueError:
        pass  # 非主线程（测试）：stop_event 已够用
    (ensure or ensure_graphify)()

    lock = acquire_singleton_lock()
    if lock is None:
        try:
            pid = lock_path().read_text(encoding="utf-8").strip()
        except OSError:
            pid = "?"
        raise GraphwatchError(f"已有 graphwatch 实例在运行（PID {pid}，锁: {lock_path()}）")

    if rebuild_runner is None:
        rebuild_runner = _run_update
    if listener_factory is None:
        listener_factory = _watchdog_listener
    if notifier is None:
        notifier = Notifier()

    log_path().parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path(), "a", encoding="utf-8")
    run_daemon._log_file = log_file

    def _dlog(msg: str) -> None:
        # 只走 stderr：服务模式 launchd 的 StandardErrorPath 重定向进日志文件，
        # 再写 log_file 会同一条记两遍；前台跑则直接给用户看
        import datetime

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] {msg}", file=sys.stderr)

    listeners: dict[str, object] = {}
    workers: list[threading.Thread] = []
    work: queue.Queue[str] = queue.Queue()
    queued: set[str] = set()
    in_flight: set[str] = set()
    dirty: set[str] = set()
    state_lock = threading.Lock()

    def _enqueue(folder: str) -> None:
        """重建入队：已在队列/执行中则标 dirty，完成后自动重跑。"""
        with state_lock:
            if folder in queued or folder in in_flight:
                dirty.add(folder)
                return
            queued.add(folder)
        work.put(folder)

    def _worker() -> None:
        while not stop_event.is_set():
            try:
                folder = work.get(timeout=0.5)
            except queue.Empty:
                continue
            with state_lock:
                queued.discard(folder)
                in_flight.add(folder)
            _dlog(f"重建开始: {folder}")
            try:
                rc = rebuild_runner(folder)
            except Exception as e:  # noqa: BLE001
                _dlog(f"重建异常: {folder}: {type(e).__name__}: {e}")
                rc = 1
            with state_lock:
                in_flight.discard(folder)
                redo = folder in dirty
                dirty.discard(folder)
            if rc != 0:
                _dlog(f"重建失败（rc={rc}）: {folder}")
                sent = notifier.fire(folder, "graphwatch 重建失败", f"{folder}\n重建退出码 {rc}，稍后变更会重试")
                _dlog(f"失败通知{'已发送' if sent else '发送失败（系统通知不可用）'}: {folder}")
            else:
                _dlog(f"重建完成: {folder}")
            if redo and not stop_event.is_set():
                _enqueue(folder)

    def _spawn_workers(n: int) -> None:
        for _ in range(n):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            workers.append(t)

    def _start_listening(folders: list[str], debounce: float) -> None:
        for f in folders:
            if f in listeners:
                continue
            _dlog(f"开始监听 {f}")
            try:
                obs = listener_factory(f, debounce, lambda ff=f: _enqueue(ff))
                obs.start()
                listeners[f] = obs
                threading.Timer(poll_interval, lambda ff=f: _catchup(ff)).start()
            except Exception as e:  # noqa: BLE001
                _dlog(f"监听启动失败（下一轮重试）: {f}: {e}")

    def _catchup(folder: str) -> None:
        """新挂载的目录若图谱落后于源码，直接入队重建（首轮补课）。"""
        if stop_event.is_set():
            return
        if stale_trigger(folder) is not None:
            _dlog(f"补课：{folder} 图谱落后于源码，入队重建")
            _enqueue(folder)

    last_sig: tuple[tuple[str, ...], float, int] | None = None

    def _reconcile() -> None:
        nonlocal last_sig
        cfg = load_config()
        debounce = float(cfg["debounce"])
        concurrency = int(cfg["rebuild_concurrency"])
        folders = [str(f) for f in cfg["folders"]]
        sig = (tuple(folders), debounce, concurrency)
        if sig == last_sig:
            # 无配置变化：补起上轮启动失败的监听
            _start_listening(folders, debounce)
            return
        desired = set(folders)
        if last_sig is not None and last_sig[1] != debounce:
            _dlog(f"debounce 变化 {last_sig[1]} → {debounce}，重启所有监听")
            for f, obs in listeners.items():
                _stop_listener(obs)
            listeners.clear()
        if last_sig is not None and last_sig[2] != concurrency:
            _dlog(f"rebuild_concurrency 变化 {last_sig[2]} → {concurrency}，重建 worker 已按新并发增量拉起")
            _spawn_workers(concurrency - len(workers)) if concurrency > len(workers) else None
            # ponytail: 并发调小不杀在跑的 worker，只少不增；下次重启生效到底
        for f in list(listeners):
            if f not in desired:
                _dlog(f"停止监听 {f}")
                _stop_listener(listeners.pop(f))
        _start_listening(folders, debounce)
        last_sig = sig

    def _stop_listener(obs) -> None:
        try:
            obs.stop()
            obs.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    def _unexpected(e: Exception) -> None:
        # 意外错误：留痕后退出，交给服务管理器（KeepAlive/Restart）拉起
        _dlog(f"daemon 意外错误，退出待服务管理器拉起: {type(e).__name__}: {e}")

    try:
        first = load_config()
        _spawn_workers(max(1, int(first["rebuild_concurrency"])))
        _reconcile()
        _dlog(f"graphwatch daemon：{len(listeners)} 个目录在监听，重建并发 {first['rebuild_concurrency']}（Ctrl-C 退出）")
        if on_started is not None:
            on_started()
        while not stop_event.wait(poll_interval):
            rotate_log()
            try:
                _reconcile()
            except GraphwatchError as e:
                # 配置短暂非法（写坏 YAML / 写到一半）：不崩，等下一轮
                _dlog(f"配置暂不可读，跳过本轮: {e}")
            except Exception as e:  # noqa: BLE001
                _unexpected(e)
                raise
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
        raise
    finally:
        if listeners:
            _dlog(f"daemon 退出：停止 {len(listeners)} 个监听")
        for obs in listeners.values():
            _stop_listener(obs)
        release_singleton_lock(lock)
        _dlog("graphwatch daemon：已退出，监听已停，锁已释放")
        log_file.close()


def daemon_alive() -> bool:
    """本机有没有活的 graphwatch daemon：能拿到单例锁 = 没有。"""
    fd = acquire_singleton_lock()
    if fd is None:
        return True
    release_singleton_lock(fd)
    return False


def tail_log(n: int = 10) -> list[str]:
    """daemon + watch 子进程日志的末尾 n 行。"""
    p = log_path()
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:] if n > 0 else []


def stale_trigger(folder: str) -> Path | None:
    """返回触发「过期」的那个源文件（比 graph.json 新），新鲜则 None。

    daemon 启动/热加载新目录时用它补课；status/list 也用它算新鲜度。
    os.walk 原地剪掉产物/依赖目录（不递归进去——实测它们占 60-97% 的
    文件数，rglob 全枚举再过滤等于白扫），每文件只 stat 一次。
    """
    root = Path(folder)
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        return None
    gm = graph.stat().st_mtime
    excluded = {"graphify-out", ".git"} | STALE_EXCLUDED_DIRS
    import stat as _stat

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for name in filenames:
            dot = name.rfind(".")
            if dot <= 0 or dot == len(name) - 1:
                continue  # 无扩展名（含 .DS_Store 这类 dotfile）不在监听范围
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            if _stat.S_ISREG(st.st_mode) and st.st_mtime > gm:
                return Path(dirpath) / name
    return None


def folder_freshness(folder: str, daemon_running: bool | None = None) -> tuple[str, str]:
    """单目录图谱新鲜度：(状态, 详情)。

    图谱产物 graphify-out/graph.json 比源目录最新改动旧 → stale；
    daemon 在跑（watch 监听会自动重建）时显示「更新中」，没跑才是「过期」。
    daemon_running=None 时现场探测。
    """
    import datetime

    root = Path(folder)
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        return "skip", "无图谱（尚未构建）"
    if stale_trigger(folder) is not None:
        if daemon_running is None:
            daemon_running = daemon_alive()
        if daemon_running:
            return "updating", "更新中（源码有变更，watch 自动重建）"
        return "fail", "图谱过期（源码有更新改动）"
    ts = datetime.datetime.fromtimestamp(graph.stat().st_mtime).strftime("%m-%d %H:%M")
    return "ok", f"新鲜（构建于 {ts}）"
