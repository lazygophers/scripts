"""graphwatch — graphify 全局 watch 守护服务的核心逻辑。

注册表（add / remove / list）+ 配置文件读写。daemon、config 向导、
服务注册在后续 ticket 里补，本模块只放它们的公共地基：
配置路径解析、原子写 0600、graphify 依赖探测。
"""

from __future__ import annotations

import os
import sys
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
    """graphify 库可用性探测：缺席时给安装指引而不是神秘 traceback。"""
    try:
        import graphify  # noqa: F401
    except ImportError as e:
        raise GraphwatchError(
            "graphify 库未安装。graphwatch 只用它、不自己解析代码，请装上：\n"
            "  pip install '.[graphify]'   # 或 '.[all]'"
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


def run_daemon(stop_event=None, ensure=None, watch_fn=None, poll_interval: float = 2.0) -> None:
    """前台守护进程：每目录一线程复用 graphify watch，单例锁防双开。

    stop_event 主要给测试用；正常路径靠 KeyboardInterrupt 退出。
    poll_interval 是主循环醒来的间隔（04 票热加载在此基础上检测配置变化）。
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

    if watch_fn is None:
        from graphify.watch import watch as watch_fn

    cfg = load_config()
    debounce = float(cfg["debounce"])
    folders = [str(f) for f in cfg["folders"]]
    if folders:
        print(f"graphwatch daemon：监听 {len(folders)} 个目录（debounce {debounce}s）", file=sys.stderr)
    else:
        print("graphwatch daemon：暂无注册目录，等待 add（Ctrl-C 退出）", file=sys.stderr)

    threads: list[threading.Thread] = []
    for folder in folders:
        t = threading.Thread(target=watch_fn, args=(Path(folder), debounce),
                             name=f"graphwatch:{folder}", daemon=True)
        t.start()
        threads.append(t)

    try:
        while not stop_event.wait(poll_interval):
            pass
    finally:
        release_singleton_lock(lock)
        print("graphwatch daemon：已退出，锁已释放", file=sys.stderr)


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

