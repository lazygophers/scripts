"""项目编译检测（Go / Node.js）。"""
import os
import re
from pathlib import Path
from typing import Callable, Optional

from lib.ui import reporter


class BuildError(RuntimeError):
    """编译异常。"""


def _go_build(dir_path: Path, *, log: Optional[Callable[[str], None]] = None) -> None:
    from .exec import run
    if log is not None:
        log(f"go build: {dir_path}")
    p = run(["go", "build", "-v", "-o", os.devnull, "."], cwd=str(dir_path), check=False, capture_output=True)
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        raise BuildError(f"编译失败: {dir_path}\n{out}".rstrip())


def _is_excluded_project(name: str) -> bool:
    return name == "pay-core" or "dao-" in name


def _is_main_package(dir_path: Path) -> bool:
    """检查目录是否是 Go main 包。"""
    go_files = sorted(dir_path.glob("*.go"))
    if not go_files:
        return False
    for f in go_files:
        for line in f.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue
            m = re.match(r"^package\s+(\S+)", stripped)
            if m:
                return m.group(1) == "main"
    return False


def _build_go_project(project_dir: Path, *, log: Optional[Callable[[str], None]] = None) -> None:
    """编译 Go 项目：编译 cmd/app 自身及子目录中的 main 包，再编译根目录。"""
    if log is not None:
        log(f"检测到 Go 项目: {project_dir.name}")
    for d in ("cmd", "app"):
        sub_dir = project_dir / d
        if sub_dir.is_dir():
            if _is_main_package(sub_dir):
                _go_build(sub_dir, log=log)
            for sub in sorted(sub_dir.iterdir()):
                if sub.is_dir() and _is_main_package(sub):
                    _go_build(sub, log=log)

    if not _is_excluded_project(project_dir.name) and _is_main_package(project_dir):
        _go_build(project_dir, log=log)


def check_build(*, project_dir: Path = Path("."), log: Optional[Callable[[str], None]] = None) -> None:
    """智能检测项目类型并执行编译检查。

    支持 Go 项目（go.mod）和 Node.js 项目（package.json）。

    Raises:
        BuildError: 当编译失败时
    """
    project_dir = project_dir.resolve()
    go_mod = project_dir / "go.mod"
    pkg_json = project_dir / "package.json"

    if go_mod.exists():
        _build_go_project(project_dir, log=log)
    elif pkg_json.exists() and log is not None:
        log(f"检测到 Node.js 项目: {project_dir.name}（跳过编译）")


def run_checkwork() -> int:
    """执行编译检查并播报结果。供 bin/checkwork 薄壳调用。"""
    from lib.notify import notify_via_n, project_done_message

    r = reporter(stderr=True)
    r.rule("编译检查", style="blue")
    r.step("开始编译检查...")

    try:
        check_build(project_dir=Path("."), log=r.step)
    except BuildError as e:
        r.err(f"编译失败\n{e}")
        return 2

    r.ok("编译检查通过")
    notify_via_n(project_done_message("编译检查完成"))
    return 0
