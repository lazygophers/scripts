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

# 默认值即 schema：folders 由 add/remove 管，其余字段 config 向导（02 票）读写。
DEFAULTS: dict = {
    "folders": [],
    "backend": "",
    "api_key": "",
    "base_url": "",
    "model": "",
    "debounce": DEFAULT_DEBOUNCE,
}


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
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
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
            "  pip install '.[graphify]'   # 或 '.[all]'"
        ) from e
    try:
        import watchdog  # noqa: F401
    except ImportError as e:
        raise GraphwatchError(
            "watchdog 未安装（graphify watch 的监听依赖，缺了子进程起不来）：\n"
            "  pip install watchdog"
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


def run_wizard(input_fn=None) -> dict:
    """引导式配置向导：逐项问 backend/api_key/base_url/model/debounce。

    每步显示当前值，回车跳过；走完才一次性落盘（中途 Ctrl-C 不产生半写配置）。
    目录列表只展示不修改（由 add/remove 管）。
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
    """系统弹窗通知（无语音）。失败返回 False，不抛——通知是尽力而为。"""
    import subprocess

    if runner is None:
        runner = subprocess.run
    quoted = message.replace('"', "'")
    tquoted = title.replace('"', "'")
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", f'display notification "{quoted}" with title "{tquoted}"']
    elif sys.platform.startswith("linux"):
        cmd = ["notify-send", title, message]
    else:
        # win32：PowerShell 气泡通知，无需额外安装
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Warning;"
            f"$n.Visible = $true; $n.ShowBalloonTip(5000, '{tquoted}', '{quoted}', 'Warning');"
            "Start-Sleep -Seconds 6; $n.Dispose()"
        )
        cmd = ["powershell", "-NoProfile", "-Command", ps]
    try:
        return runner(cmd, check=False, capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


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
        import datetime

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, file=sys.stderr)
        log_file.write(line + "\n")
        log_file.flush()

    children: dict[str, object] = {}
    last_sig: tuple[tuple[str, ...], float] | None = None

    def _reconcile() -> None:
        nonlocal last_sig
        cfg = load_config()
        debounce = float(cfg["debounce"])
        folders = [str(f) for f in cfg["folders"]]
        sig = (tuple(folders), debounce)
        if sig == last_sig:
            # 无配置变化：只处理崩溃重启
            for f, proc in list(children.items()):
                rc = proc.poll()
                if rc is not None:
                    _dlog(f"监听进程退出（rc={rc}），重启 {f}")
                    notifier.fire(f, "graphwatch 重建失败", f"{f}\n监听进程退出（rc={rc}），已自动重启")
                    children[f] = watch_factory(debounce)(Path(f))
            return
        desired = set(folders)
        if last_sig is not None and last_sig[1] != debounce:
            # debounce 变了：所有子进程按旧参数起的，全部重启
            for f, proc in children.items():
                _stop_child(proc, f)
            children.clear()
        for f in list(children):
            if f not in desired:
                _dlog(f"停止监听 {f}")
                _stop_child(children.pop(f), f)
        start_fn = watch_factory(debounce)
        for f in folders:
            if f not in children:
                _dlog(f"开始监听 {f}")
                children[f] = start_fn(Path(f))
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
    finally:
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
        table.add_column("目录")
        for f in folders:
            table.add_row(f)
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
    def uninstall(self) -> int:
        """停止并删除服务注册（配置与日志保留）。

        用法: graphwatch uninstall
        """
        uninstall_service()
        self._r.ok("服务已注销（配置与日志保留）")
        return 0

    @_cmd
    def status(self) -> int:
        """查看服务注册态、进程存活、各目录图谱新鲜度。

        用法: graphwatch status
        """
        from rich.table import Table

        rows: list[tuple] = []
        installed = service_registered()
        alive = daemon_alive() if installed else False
        head = "已注册" if installed else "未注册"
        head += " · 进程在跑" if alive else (" · 进程没在跑" if installed else "")
        if not installed:
            self._r.warn("服务未注册。先: graphwatch install")
        for f in list_folders():
            st, detail = folder_freshness(f)
            rows.append((f, st, detail))
        table = Table(title=f"graphwatch 状态（服务{head}）")
        table.add_column("目录", style="bold")
        table.add_column("图谱")
        table.add_column("详情")
        for folder, st, detail in rows:
            from lib.ui import STATUS_LABEL

            color = {"ok": "green", "skip": "yellow", "fail": "red"}[st]
            label = "在监听" if st == "ok" else STATUS_LABEL.get(st, st)
            table.add_row(folder, f"[{color}]{label}[/{color}]", detail)
        self._r.console.print(table)
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
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.lazygophers.graphwatch</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{exe}</string>
    <string>run</string>
  </array>
  <key>KeepAlive</key><true/>
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


def install_service(runner=None) -> None:
    """注册为用户级服务并立即启动。按平台走 launchd / systemd / schtasks。"""
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=True, capture_output=True, **kw)
    plat = sys.platform
    if plat == "darwin":
        p = launchd_plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(launchd_plist(), encoding="utf-8")
        runner(["launchctl", "unload", str(p)], check=False)
        runner(["launchctl", "load", str(p)])
    elif plat.startswith("linux"):
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "graphwatch.service").write_text(systemd_unit(), encoding="utf-8")
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", "--now", "graphwatch.service"])
    else:
        runner(schtasks_create_command())


def uninstall_service(runner=None) -> None:
    """停止并删除服务注册。配置与日志不动。"""
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=True, capture_output=True, **kw)
    plat = sys.platform
    if plat == "darwin":
        p = launchd_plist_path()
        if p.exists():
            runner(["launchctl", "unload", str(p)], check=False)
            p.unlink()
    elif plat.startswith("linux"):
        runner(["systemctl", "--user", "disable", "--now", "graphwatch.service"])
        (Path.home() / ".config" / "systemd" / "user" / "graphwatch.service").unlink(missing_ok=True)
    else:
        runner(["schtasks", "/Delete", "/F", "/TN", "graphwatch"])


def service_registered(runner=None) -> bool:
    """服务注册态探测：launchctl / systemctl / schtasks 各查各的。"""
    import subprocess

    if runner is None:
        def runner(cmd, **kw):
            return subprocess.run(cmd, check=False, capture_output=True, **kw)
    plat = sys.platform
    if plat == "darwin":
        r = runner(["launchctl", "list", "com.lazygophers.graphwatch"])
        return r.returncode == 0
    if plat.startswith("linux"):
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


def folder_freshness(folder: str) -> tuple[str, str]:
    """单目录图谱新鲜度：(状态, 详情)。

    图谱产物 graphify-out/graph.json 比源目录最新改动旧 → stale。
    """
    import datetime

    root = Path(folder)
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        return "skip", "无图谱（尚未构建）"
    newest = graph.stat().st_mtime
    for p in root.rglob("*"):
        if "graphify-out" in p.parts or ".git" in p.parts or not p.is_file():
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > newest:
            newest = mt
            break
    if newest > graph.stat().st_mtime:
        return "fail", "图谱过期（源码有更新改动）"
    ts = datetime.datetime.fromtimestamp(graph.stat().st_mtime).strftime("%m-%d %H:%M")
    return "ok", f"新鲜（构建于 {ts}）"
