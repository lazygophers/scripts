"""忽略清单 .lazyscriptsignore：读取、校验与匹配。

批量 git 命令（merge/push/sync/switch/delete 族）扫描仓库时，被清单
命中的仓库跳过，汇总表显示 skip（详情 "ignored by .lazyscriptsignore"）。

清单按目录设置，向上继承：查某个操作类型时从仓库目录逐级向上，途经
文件没声明该类型（且 all 条目未命中）就继续向上，命中即停；上界是
扫描根（跑命令时所在目录）。清单只拦批量扫描，单仓手动命令不受约束。
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

IGNORE_FILENAME = ".lazyscriptsignore"
IGNORE_KEYS = ("merge", "push", "sync", "switch", "delete", "all")

SAMPLE = f"""\
# .lazyscriptsignore — 批量 git 命令忽略清单（lazyhelp ignore 生成）
# 值为路径列表，相对本文件所在目录；条目命中的目录及其子树内所有仓库被跳过。
# 没声明某个键时向上找父目录的清单，直到扫描根；all 通配全部操作。
# merge / push / sync / switch / delete 分别对应各命令族。
merge: []
push: []
sync: []
switch: []
delete: []
all: []
"""


class IgnoreError(Exception):
    """忽略文件本身有问题（读不了 / 语法错 / 未知键 / 值形状不对）。

    fail closed：调用方中止整个批量命令，绝不静默放行。
    """


def load_ignore_file(path: Path) -> dict[str, list[str]]:
    """读取并校验一个忽略文件，返回 key → 路径列表。"""
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise IgnoreError(f"{path}: 无法读取: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise IgnoreError(f"{path}: YAML 语法错误: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise IgnoreError(f"{path}: 顶层必须是键值映射，当前是 {type(data).__name__}")
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if key not in IGNORE_KEYS:
            raise IgnoreError(f"{path}: 未知键 {key!r}（合法键: {', '.join(IGNORE_KEYS)}）")
        if value is None:
            out[key] = []
        elif isinstance(value, list) and all(isinstance(x, str) for x in value):
            out[key] = list(value)
        else:
            raise IgnoreError(f"{path}: 键 {key} 的值必须是路径字符串列表")
    return out


def _matches(rel: Path, entries: list[str]) -> bool:
    """条目命中 = rel 自身或任一祖先前缀 fnmatch 某条目（子树 + 通配）。"""
    parts = rel.parts or (".",)
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return any(fnmatch(p, entry) for entry in entries for p in prefixes)


def find_ignore(repo: Path, op: str, root: Path) -> Path | None:
    """按操作类型向上找第一个命中 repo 的忽略文件；没有则 None。

    IgnoreError 直接抛给调用方（fail closed）。
    """
    if op not in IGNORE_KEYS or op == "all":
        raise ValueError(f"op 必须是具体操作类型，不是 all: {op!r}")
    repo = repo.resolve()
    root = root.resolve()
    d = repo
    while True:
        f = d / IGNORE_FILENAME
        if f.is_file():
            data = load_ignore_file(f)
            rel = repo.relative_to(d)
            for key in ("all", op):
                if _matches(rel, data.get(key, [])):
                    return f
        if d == root or d.parent == d:
            return None
        d = d.parent
