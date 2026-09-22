"""graphwatch 配置与注册表：graphwatch.yaml 读写 + 目录清单 + 配置向导。

纯函数层：不 import daemon / service，被两者依赖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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
    "rebuild_concurrency": 1,
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
    try:
        cfg["rebuild_concurrency"] = int(cfg["rebuild_concurrency"])
    except (TypeError, ValueError) as e:
        raise GraphwatchError(f"rebuild_concurrency 应是整数: {p}") from e
    if cfg["rebuild_concurrency"] < 1:
        raise GraphwatchError(f"rebuild_concurrency 最小为 1: {p}")
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
    ("rebuild_concurrency", "同时重建的目录数（串行=1，最小 1）", ("rebuild_concurrency", "int_min1")),
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
    if validator[1] == "int_min1":
        try:
            v = int(raw)
        except ValueError:
            return None
        return v if v >= 1 else None
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


