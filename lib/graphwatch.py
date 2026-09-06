"""graphwatch — graphify 全局 watch 守护服务的核心逻辑。

注册表（add / remove / list）+ 配置文件读写。daemon、config 向导、
服务注册在后续 ticket 里补，本模块只放它们的公共地基：
配置路径解析、原子写 0600、graphify 依赖探测。
"""

from __future__ import annotations

import os
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

