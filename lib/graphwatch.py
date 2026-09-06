"""graphwatch — graphify 全局 watch 守护服务的核心逻辑。

注册表（add / remove / list）+ 配置文件读写。daemon、config 向导、
服务注册在后续 ticket 里补，本模块只放它们的公共地基：
配置路径解析、原子写 0600、graphify 依赖探测。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from lib.fire_base import BaseCli, timed_cli

CONFIG_NAME = "graphwatch.yaml"
DEFAULT_DEBOUNCE = 3.0
LAUNCHD_LABEL = "com.lazygophers.graphwatch"

# 默认值即 schema：folders 由 add/remove 管，其余字段 config 向导（02 票）读写。
DEFAULTS: dict = {
    "folders": [],
    "backend": "",
    "api_key": "",
    "base_url": "",
    "model": "",
    "debounce": DEFAULT_DEBOUNCE,
}


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


class GraphwatchError(Exception):
    """graphwatch 自己的错误：CLI 层转一行人话，不甩 traceback。"""


def config_home() -> Path:
    """配置根：GRAPHWATCH_HOME 可重定向（测试缝），默认与 archery 同目录。"""
    env = os.environ.get("GRAPHWATCH_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "lazygophers" / "scripts"


def config_path() -> Path:
    return config_home() / CONFIG_NAME


def load_config() -> dict:
    """读配置，缺文件或字段时回落 DEFAULTS（浅合并，够用）。"""
    import copy

    import yaml

    cfg = copy.deepcopy(DEFAULTS)
    p = config_path()
    if p.is_file():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise GraphwatchError(f"配置文件不是合法 YAML: {p}\n{e}") from e
        if not isinstance(data, dict):
            raise GraphwatchError(f"配置文件顶层应是映射: {p}")
        unknown = [k for k in data if k not in DEFAULTS]
        if unknown:
            print(f"配置里有未知字段（忽略）: {', '.join(unknown)}", file=sys.stderr)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    folders = cfg["folders"]
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        raise GraphwatchError(f"folders 应是目录路径列表: {p}")
    try:
        cfg["debounce"] = float(cfg["debounce"])
    except (TypeError, ValueError) as e:
        raise GraphwatchError(f"debounce 应是数字: {p}") from e
    if cfg["debounce"] <= 0:
        raise GraphwatchError(f"debounce 应大于 0: {p}")
    return cfg


def save_config(cfg: dict) -> None:
    """原子写 + 0600：临时文件写完 chmod，再 os.replace 换名（同 archery 惯例）。"""
    import tempfile

    import yaml

    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    target = config_path()
    text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".graphwatch-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(target, 0o600)


def _normalize(path: Path) -> str:
    """注册表里存 resolve 后的绝对路径字符串，add/remove 都过这一层去重。"""
    return str(path.expanduser().resolve())


def add_folder(path: Path) -> str:
    p = Path(path)
    if not p.exists():
        raise GraphwatchError(f"目录不存在: {p}")
    if not p.is_dir():
        raise GraphwatchError(f"不是目录: {p}")
    cfg = load_config()
    stored = _normalize(p)
    folders = [str(f) for f in cfg["folders"]]
    if stored not in folders:
        folders.append(stored)
        cfg["folders"] = folders
        save_config(cfg)
    return stored


def remove_folder(path: Path) -> str:
    cfg = load_config()
    stored = _normalize(Path(path))
    folders = [str(f) for f in cfg["folders"]]
    if stored not in folders:
        raise GraphwatchError(f"未注册过该目录: {stored}")
    folders.remove(stored)
    cfg["folders"] = folders
    save_config(cfg)
    return stored


def list_folders() -> list[str]:
    return [str(f) for f in load_config()["folders"]]


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


def mask_secret(value: str) -> str:
    """脱敏：≥8 字符露前 4 后 4，短的全遮，空串原样。"""
    if not value:
        return ""
    if len(value) < 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


# 后端清单抄自 graphify/llm.py 的 BACKENDS（claude/kimi/ollama/gemini/openai/
# deepseek/azure/bedrock/claude-cli）；openai 即 OpenAI 标准协议，配 base_url
# 可指向任意兼容端点（LiteLLM、网关、中转等）。
BACKEND_CHOICES: list[str] = [
    "claude", "kimi", "gemini", "openai", "deepseek", "ollama", "azure", "bedrock", "claude-cli",
]

# 向导步骤：(配置键, 提示, 校验/转换)。回车 = 保留当前值。
_WIZARD_STEPS: list[tuple[str, str, tuple[str, str] | None]] = [
    ("backend", "AI 后端（备用字段，重建不用 LLM；输入编号选择）", ("backend", "choice")),
    ("api_key", "API key（写入 0600 配置文件）", None),
    ("base_url", "base URL（留空用官方默认；openai 协议常需要填）", None),
    ("model", "模型名（留空用后端默认）", None),
    ("debounce", "防抖秒数（变更后等多久再重建）", ("debounce", "float_positive")),
]


def _parse_step(raw: str, current, validator):
    """单步解析：回车保留当前值；按 validator 校验，非法返回 None 重问。"""
    if not raw.strip():
        return current
    if validator is None:
        return raw.strip()
    if validator[1] == "choice":
        try:
            idx = int(raw.strip())
        except ValueError:
            return None
        return BACKEND_CHOICES[idx - 1] if 1 <= idx <= len(BACKEND_CHOICES) else None
    if validator[1] == "float_positive":
        try:
            v = float(raw)
        except ValueError:
            return None
        return v if v > 0 else None
    return raw.strip()


def run_wizard(input_fn=None, picker=None) -> dict:
    """引导式配置向导：逐项问 backend/api_key/base_url/model/debounce。

    每步显示当前值，回车跳过；走完才一次性落盘（中途 Ctrl-C 不产生半写配置）。
    目录列表只展示不修改（由 add/remove 管）。picker 是按键注入缝（测试用），
    传 None 时 ask_select 在无 TTY 下自回退到编号输入。
    """
    if input_fn is None:
        input_fn = input
    cfg = load_config()
    print("graphwatch 配置向导 — 回车保留当前值，Ctrl-C 放弃全部修改\n", file=sys.stderr)
    folders = [str(f) for f in cfg["folders"]]
    print(f"已注册目录（{len(folders)} 个，向导不改目录，用 add/remove）：", file=sys.stderr)
    for f in folders:
        print(f"  {f}", file=sys.stderr)
    print(file=sys.stderr)

    for key, prompt, validator in _WIZARD_STEPS:
        current = cfg[key]
        shown = mask_secret(current) if key == "api_key" else current
        if validator is not None and validator[1] == "choice":
            # TTY 下走方向键 + 数字快捷键菜单，Esc = 保留当前值；
            # 非交互（测试/管道）回退编号文本输入
            from lib.ui import ask_select

            if picker is not None or sys.stdin.isatty():
                picked = ask_select("AI 后端（备用字段，重建不用 LLM）", BACKEND_CHOICES,
                                    current=current or None, read_key=picker)
                cfg[key] = picked if picked is not None else current
                continue
            print(f"{prompt}:", file=sys.stderr)
            for i, opt in enumerate(BACKEND_CHOICES, 1):
                mark = " ←当前" if opt == current else ""
                print(f"  {i}. {opt}{mark}", file=sys.stderr)
            raw = input_fn(f"选择 1-{len(BACKEND_CHOICES)}（回车保留 {shown!r}）: ")
        else:
            raw = input_fn(f"{prompt} [{shown!r}]: ")
        while True:
            parsed = _parse_step(raw, current, validator)
            if parsed is not None:
                cfg[key] = parsed
                break
            print("  无效输入，请重试", file=sys.stderr)
            raw = input_fn(f"{prompt} [{shown!r}]: ")

    save_config(cfg)
    print(f"\n✓ 已写入 {config_path()}", file=sys.stderr)
    return cfg


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


def _spawn_watcher_factory(debounce: float):
    """默认 watch 工厂：每目录起一个 `python -m graphify watch` 子进程。

    走公共 CLI 而不是 import graphify 内部函数（watch() 的循环只认
    KeyboardInterrupt，线程停不掉）；子进程 terminate 即干净停监听，
    崩溃由 daemon 主循环拉起。
    """
    import subprocess

    def start(folder: Path):
        return subprocess.Popen(
            [sys.executable, "-m", "graphify", "watch", str(folder), "--debounce", str(debounce)],
            stdout=getattr(run_daemon, "_log_file", None) or sys.stderr,
            stderr=getattr(run_daemon, "_log_file", None) or sys.stderr,
        )

    return start


def _stop_child(proc, folder: str) -> None:
    """停一个监听子进程：terminate → 等 5s → kill。"""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def run_daemon(stop_event=None, ensure=None, watch_factory=None, poll_interval: float = 2.0,
               on_started=None, notifier=None) -> None:
    """前台守护进程：监督每目录一个监听子进程，全局单例锁防双开。

    主循环每 poll_interval 醒一次：读配置（坏 YAML 跳过本轮不崩），
    目录列表或 debounce 变化时增删/重启子进程，子进程崩溃自动拉起。
    stop_event 主要给测试用；正常路径靠 KeyboardInterrupt 退出。
    """
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

    if watch_factory is None:
        watch_factory = _spawn_watcher_factory
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

    children: dict[str, object] = {}
    started_logged: set[str] = set()
    last_sig: tuple[tuple[str, ...], float] | None = None

    def _nudge_if_stale(folder: str) -> None:
        """graphify watch 没有首轮构建：图谱落后于源码时 touch 过期文件、
        重放变更事件触发重建。幂等——新鲜目录不动。"""
        t = stale_trigger(folder)
        if t is None:
            return
        try:
            os.utime(t)
            _dlog(f"补课：{folder} 图谱落后于源码，touch {t.name} 触发重建")
        except OSError as e:
            _dlog(f"补课失败: {folder}: {e}")

    def _start_missing(debounce: float, folders: list[str]) -> None:
        """补起缺的监听子进程（首次或上一轮启动失败的重试）。

        每个新起的子进程都安排一次延迟补课（启动 / 热加载新增目录 /
        崩溃重启统一走这里）：等一个 poll 周期让 watch 的监听就位，
        再检测过期并触发首轮重建。
        """
        import threading

        start_fn = watch_factory(debounce)
        for f in folders:
            if f in children:
                continue
            if f not in started_logged:
                _dlog(f"开始监听 {f}")
            try:
                children[f] = start_fn(Path(f))
                started_logged.add(f)
                threading.Timer(poll_interval, _nudge_if_stale, args=(f,)).start()
            except Exception as e:  # noqa: BLE001
                _dlog(f"监听进程启动失败（下一轮重试）: {f}: {e}")

    def _reconcile() -> None:
        nonlocal last_sig
        cfg = load_config()
        debounce = float(cfg["debounce"])
        folders = [str(f) for f in cfg["folders"]]
        sig = (tuple(folders), debounce)
        if sig == last_sig:
            # 无配置变化：处理崩溃重启 + 补起上轮启动失败的
            for f, proc in list(children.items()):
                rc = proc.poll()
                if rc is not None:
                    _dlog(f"监听进程退出（rc={rc}），重启 {f}")
                    sent = notifier.fire(f, "graphwatch 重建失败", f"{f}\n监听进程退出（rc={rc}），已自动重启")
                    _dlog(f"崩溃通知{'已发送' if sent else '发送失败（系统通知不可用）'}: {f}")
                    del children[f]
            _start_missing(debounce, folders)
            return
        desired = set(folders)
        if last_sig is not None and last_sig[1] != debounce:
            # debounce 变了：所有子进程按旧参数起的，全部重启
            _dlog(f"debounce 变化 {last_sig[1]} → {debounce}，重启所有监听")
            for f, proc in children.items():
                _stop_child(proc, f)
            children.clear()
        for f in list(children):
            if f not in desired:
                _dlog(f"停止监听 {f}")
                _stop_child(children.pop(f), f)
                started_logged.discard(f)
        _start_missing(debounce, folders)
        last_sig = sig

    try:
        _reconcile()
        _dlog(f"graphwatch daemon：{len(children)} 个目录在监听（Ctrl-C 退出）")
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
                # 意外错误：留痕后退出，交给服务管理器（KeepAlive/Restart）拉起
                _dlog(f"daemon 意外错误，退出待服务管理器拉起: {type(e).__name__}: {e}")
                raise
    finally:
        if children:
            _dlog(f"daemon 退出：停止 {len(children)} 个监听子进程")
        for f, proc in children.items():
            _stop_child(proc, f)
        release_singleton_lock(lock)
        _dlog("graphwatch daemon：已退出，监听子进程已停，锁已释放")
        log_file.close()


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
    """graphwatch CLI。子命令：add / remove / list（config、run、service 后续票补）。"""

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



# === 服务注册（06 票）：全部用户级，无需 root ===

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


def script_path() -> Path:
    """本仓 bin/graphwatch 绝对路径——服务里就跑它。"""
    return Path(__file__).resolve().parent.parent / "bin" / "graphwatch"


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.lazygophers.graphwatch.plist"


def launchd_plist() -> str:
    """LaunchAgent plist：KeepAlive 崩了自动拉起，RunAtLoad 登录自启。"""
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


def install_service(runner=None) -> None:
    """注册为用户级服务并立即启动。按平台走 launchd / systemd / schtasks。"""
    if runner is None:
        runner = _checked_runner()
    plat = sys.platform
    if plat == "darwin":
        p = launchd_plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(launchd_plist(), encoding="utf-8")
        _launchd_stop(runner)
        _launchd_start(runner, str(p))
    elif plat.startswith("linux"):
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "graphwatch.service").write_text(systemd_unit(), encoding="utf-8")
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", "--now", "graphwatch.service"])
    else:
        runner(schtasks_create_command())


def service_control(action: str, runner=None) -> None:
    """start / stop / restart 已注册的服务。stop 不动注册。"""
    if runner is None:
        runner = _checked_runner()
    if action not in ("start", "stop", "restart"):
        raise GraphwatchError(f"未知动作 {action!r}，可用: start / stop / restart")
    if not service_registered():
        raise GraphwatchError("服务未注册，先: graphwatch install")
    plat = sys.platform
    if plat == "darwin":
        # 现代 launchd API：bootstrap/bootout（旧 load/unload 会 "Unload failed: 5"）
        p = str(launchd_plist_path())
        if action in ("stop", "restart"):
            _launchd_stop(runner)
        if action in ("start", "restart"):
            _launchd_start(runner, p)
    elif plat.startswith("linux"):
        runner(["systemctl", "--user", action, "graphwatch.service"])
    else:
        if action in ("stop", "restart"):
            runner(["schtasks", "/End", "/TN", "graphwatch"], check=False)
        if action in ("start", "restart"):
            runner(["schtasks", "/Run", "/TN", "graphwatch"])


def uninstall_service(runner=None) -> None:
    """停止并删除服务注册。配置与日志不动。"""
    if runner is None:
        runner = _checked_runner()
    plat = sys.platform
    if plat == "darwin":
        p = launchd_plist_path()
        _launchd_stop(runner)
        if p.exists():
            p.unlink()
    elif plat.startswith("linux"):
        runner(["systemctl", "--user", "disable", "--now", "graphwatch.service"])
        (Path.home() / ".config" / "systemd" / "user" / "graphwatch.service").unlink(missing_ok=True)
    else:
        runner(["schtasks", "/Delete", "/F", "/TN", "graphwatch"])


def service_registered(runner=None) -> bool:
    """服务注册态探测。

    macOS 注册态 = plist 文件存在（stop 会 bootout 出 domain，但注册保留、
    start 可重新 bootstrap）；systemd 的 is-enabled / schtasks 的 Query 本身
    就是磁盘态，无此问题。
    """
    if sys.platform == "darwin":
        return launchd_plist_path().is_file()
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=False, capture_output=True, **kw)
    if sys.platform.startswith("linux"):
        r = runner(["systemctl", "--user", "is-enabled", "graphwatch.service"])
        return r.returncode == 0
    r = runner(["schtasks", "/Query", "/TN", "graphwatch"])
    return r.returncode == 0


def daemon_alive() -> bool:
    """本机有没有活的 graphwatch daemon：能拿到单例锁 = 没有。"""
    fd = acquire_singleton_lock()
    if fd is None:
        return True
    release_singleton_lock(fd)
    return False


def service_state(runner=None) -> dict:
    """服务执行态：注册 / 运行 / PID / 上次退出码 / 运行时长。"""
    state = {"registered": service_registered(runner), "running": False,
             "pid": "-", "last_exit": "-", "uptime": "-"}
    if not state["registered"]:
        return state
    import re
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            kw.setdefault("check", False)
            return subprocess.run(cmd, capture_output=True, **kw)
    plat = sys.platform
    if plat == "darwin":
        if not _launchd_gone(runner):
            r = runner(_sh("launchctl", "print", f"{_launchd_domain()}/{LAUNCHD_LABEL}"), check=False)
            text = getattr(r, "stdout", b"").decode(errors="replace") if isinstance(getattr(r, "stdout", None), bytes) else str(getattr(r, "stdout", "") or "")
            pid = re.search(r"^\s*pid = (\d+)", text, re.M)
            state["running"] = pid is not None
            state["pid"] = pid.group(1) if pid else "-"
            lex = re.search(r"^\s*last exit code = (.+)$", text, re.M)
            state["last_exit"] = lex.group(1).strip() if lex else "-"
    elif plat.startswith("linux"):
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
    else:
        state["running"] = daemon_alive()
    # ponytail: 运行时长留 "-"——macOS 无跨版本稳的启动时间源，需要 ps -o etime 时再加
    return state


def tail_log(n: int = 10) -> list[str]:
    """daemon + watch 子进程日志的末尾 n 行。"""
    p = log_path()
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:] if n > 0 else []


def stale_trigger(folder: str) -> Path | None:
    """返回触发「过期」的那个源文件（比 graph.json 新），新鲜则 None。

    daemon 给新起的 watch 子进程补课时 touch 它，重放变更事件触发重建。
    """
    root = Path(folder)
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        return None
    gm = graph.stat().st_mtime
    excluded = {"graphify-out", ".git"} | STALE_EXCLUDED_DIRS
    for p in root.rglob("*"):
        # 跳过产物/依赖目录：不比对了，整棵子树都不是「源码改动」
        if any(part in excluded for part in p.parts):
            continue
        if not p.is_file() or not p.suffix:
            # 无扩展名文件不在 graphify watch 的监听范围（_WATCHED_EXTENSIONS
            # 按 suffix 过滤），变更不触发 watch 重建，统计它们只会造成永久假过期
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > gm:
            return p
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
