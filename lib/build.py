"""项目编译检测（CI/CD build 前置拦截）。

支持 Go / Rust / Python / Java / C/C++ / Node.js，按 lockfile 自动选包管理器。
检查命令带 verbose 标志，透传输出让用户实时看到进度。零产物：check-only 或 build 到 /dev/null。
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lib.exec import run, run_no_capture
from lib.ui import reporter


class BuildError(RuntimeError):
    """编译异常。"""


@dataclass
class CheckResult:
    """单检查点结果。"""
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "fail"


def _have(cmd: str) -> bool:
    """命令是否在 PATH 中可用。"""
    from shutil import which
    return which(cmd) is not None


def _count_issue_lines(output: str) -> int:
    """从 ruff/mypy concise 输出统计告警条数（非空、非统计行）。"""
    n = 0
    for line in (output or "").splitlines():
        s = line.strip()
        if not s or s.startswith("Found"):
            continue
        n += 1
    return n


def _count_type_errors(output: str) -> int:
    """从 tsc/类型检查输出统计错误条数（含 'error' 的行）。"""
    return sum(1 for line in (output or "").splitlines() if "error" in line.lower())


def _run_verbose(cmd: Sequence[str], *, cwd: str | None = None,
                 log: Callable[[str], None] | None = None) -> int:
    """透传 stdout/stderr 执行命令（verbose 进度），返回退出码。"""
    from lib.exec import shell_join
    if log is not None:
        log(f"执行: {shell_join(cmd)}")
    return run_no_capture(cmd, cwd=cwd)


# === Go ===

def _go_build(dir_path: Path, *, log: Callable[[str], None] | None = None) -> None:
    """go build -v -o /dev/null（verbose 透传，零产物）。"""
    if log is not None:
        log(f"go build: {dir_path}")
    rc = run_no_capture(["go", "build", "-v", "-o", os.devnull, "."], cwd=str(dir_path))
    if rc != 0:
        raise BuildError(f"编译失败: {dir_path} (exit={rc})")


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


def _check_go_project(project_dir: Path, *,
                      log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """编译 Go 项目：编译 cmd/app 自身及子目录中的 main 包，再编译根目录。"""
    results: list[CheckResult] = []
    targets: list[Path] = []

    for d in ("cmd", "app"):
        sub_dir = project_dir / d
        if sub_dir.is_dir():
            if _is_main_package(sub_dir):
                targets.append(sub_dir)
            for sub in sorted(sub_dir.iterdir()):
                if sub.is_dir() and _is_main_package(sub):
                    targets.append(sub)

    if not _is_excluded_project(project_dir.name) and _is_main_package(project_dir):
        targets.append(project_dir)

    for t in targets:
        try:
            _go_build(t, log=log)
            results.append(CheckResult(f"go build: {t.name}", "ok"))
        except BuildError as e:
            results.append(CheckResult(f"go build: {t.name}", "fail", str(e)))
    return results


# === Rust ===

def _check_rust_project(project_dir: Path, *,
                        log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """cargo check（不 build，不产生二进制）。"""
    cargo = "cargo"
    if log is not None:
        log(f"cargo check: {project_dir.name}")
    rc = _run_verbose([cargo, "check", "--verbose"], cwd=str(project_dir), log=log)
    status = "ok" if rc == 0 else "fail"
    return [CheckResult("cargo check", status, "" if rc == 0 else f"exit={rc}")]


# === Python ===

def _check_python_project(project_dir: Path, *,
                          log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """py_compile 语法编译（致命），mypy/ruff warn-only。"""
    results: list[CheckResult] = []

    # py_compile 批量编译：顶层 + src/
    py_dirs = [project_dir]
    src_dir = project_dir / "src"
    if src_dir.is_dir():
        py_dirs.append(src_dir)

    py_files: list[Path] = []
    for d in py_dirs:
        py_files.extend(sorted(d.glob("*.py")))

    if py_files:
        if log is not None:
            log(f"py_compile: {len(py_files)} 个 .py 文件")
        import py_compile
        errors: list[str] = []
        for f in py_files:
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(str(e))
        if errors:
            results.append(CheckResult("py_compile", "fail", "\n".join(errors)))
        else:
            results.append(CheckResult("py_compile", "ok"))

    # mypy warn-only（静默: capture 输出, 只报条数, 不刷屏）
    if _have("mypy") and py_files:
        if log is not None:
            log("mypy 类型检查（warn-only）")
        p = run(["mypy", "--no-error-summary"], cwd=str(project_dir), check=False, capture_output=True)
        if p.returncode == 0:
            results.append(CheckResult("mypy", "ok"))
        else:
            n = _count_issue_lines((p.stdout or "") + (p.stderr or ""))
            results.append(CheckResult("mypy", "warn", f"{n} 项类型告警（仅警告，不中止）"))

    # ruff warn-only（静默: capture 输出, 只报条数, 不刷屏）
    if _have("ruff"):
        if log is not None:
            log("ruff lint 检查（warn-only）")
        p = run(["ruff", "check", ".", "--output-format=concise"],
                cwd=str(project_dir), check=False, capture_output=True)
        if p.returncode == 0:
            results.append(CheckResult("ruff", "ok"))
        else:
            n = _count_issue_lines((p.stdout or "") + (p.stderr or ""))
            results.append(CheckResult("ruff", "warn", f"{n} 项 lint 告警（仅警告，不中止）"))

    return results


# === Java (gradle/maven) ===

def _check_java_project(project_dir: Path, *,
                        log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """gradle: ./gradlew compileJava; maven: mvn compile。产物留项目自管 build/target。"""
    gradle = project_dir / "build.gradle"
    gradle_kts = project_dir / "build.gradle.kts"
    pom = project_dir / "pom.xml"

    if gradle.exists() or gradle_kts.exists():
        wrapper = project_dir / "gradlew"
        gcmd = ["./gradlew"] if wrapper.exists() and os.access(wrapper, os.X_OK) else ["gradle"]
        rc = _run_verbose([*gcmd, "compileJava", "--console=plain", "--warning-mode=all"],
                          cwd=str(project_dir), log=log)
        return [CheckResult("gradle compileJava", "ok" if rc == 0 else "fail",
                            "" if rc == 0 else f"exit={rc}")]

    if pom.exists():
        mcmd = "mvn"
        rc = _run_verbose([mcmd, "compile", "-q"], cwd=str(project_dir), log=log)
        return [CheckResult("mvn compile", "ok" if rc == 0 else "fail",
                            "" if rc == 0 else f"exit={rc}")]

    return []


# === C/C++ (make/cmake) ===

def _check_cc_project(project_dir: Path, *,
                      log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """make: dry-run（-n，不真正 build）; cmake: 构建到 $TMPDIR 后清理。"""
    makefile = project_dir / "Makefile"
    cmakelists = project_dir / "CMakeLists.txt"

    if makefile.exists():
        if log is not None:
            log("make dry-run (-n, 不产生产物)")
        rc = _run_verbose(["make", "-n"], cwd=str(project_dir), log=log)
        return [CheckResult("make -n", "ok" if rc == 0 else "fail",
                            "" if rc == 0 else f"exit={rc}")]

    if cmakelists.exists():
        import shutil
        import tempfile
        tmpdir = Path(tempfile.mkdtemp(prefix="checkwork_cmake_"))
        try:
            rc1 = _run_verbose(["cmake", "-S", str(project_dir), "-B", str(tmpdir)],
                               log=log)
            if rc1 != 0:
                return [CheckResult("cmake configure", "fail", f"exit={rc1}")]
            rc2 = _run_verbose(["cmake", "--build", str(tmpdir), "--verbose"],
                               log=log)
            return [CheckResult("cmake build", "ok" if rc2 == 0 else "fail",
                                "" if rc2 == 0 else f"exit={rc2}")]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            if log is not None:
                log(f"已清理临时构建目录: {tmpdir}")

    return []


# === Node.js ===

def _detect_node_pkg_manager(project_dir: Path) -> str | None:
    """按 lockfile 优先级选包管理器。"""
    if (project_dir / "bun.lockb").exists() or (project_dir / "bun.lock").exists():
        return "bun"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "package-lock.json").exists():
        return "npm"
    # 无 lockfile，探测可用的
    for pm in ("bun", "yarn", "pnpm", "npm"):
        if _have(pm):
            return pm
    return None


def _read_package_scripts(project_dir: Path) -> dict:
    """读 package.json 的 scripts 字段。"""
    import json
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return {}
    try:
        data = json.loads(pkg_json.read_text())
        return data.get("scripts", {}) or {}
    except (ValueError, TypeError):
        return {}


def _check_node_project(project_dir: Path, *,
                        log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """按 lockfile 选包管理器跑 build/typecheck script；tsc 类型错误只警告不中止。"""
    results: list[CheckResult] = []
    pm = _detect_node_pkg_manager(project_dir)
    scripts = _read_package_scripts(project_dir)

    if pm is None:
        if log is not None:
            log("Node.js 项目: 未检测到包管理器，跳过")
        results.append(CheckResult("node", "warn", "未检测到包管理器"))
        return results

    if log is not None:
        log(f"Node.js 项目: 包管理器 {pm}")

    # build script（致命 — build 失败 = CI 会挂）
    if "build" in scripts:
        rc = _run_verbose([pm, "run", "build"], cwd=str(project_dir), log=log)
        results.append(CheckResult(f"{pm} build", "ok" if rc == 0 else "fail",
                                   "" if rc == 0 else f"exit={rc}"))

    # typecheck script（warn-only, 静默: 类型错误不中止, 不刷屏）
    if "typecheck" in scripts:
        if log is not None:
            log(f"{pm} typecheck（warn-only）")
        p = run([pm, "run", "typecheck"], cwd=str(project_dir), check=False, capture_output=True)
        if p.returncode == 0:
            results.append(CheckResult(f"{pm} typecheck", "ok"))
        else:
            n = _count_type_errors((p.stdout or "") + (p.stderr or ""))
            results.append(CheckResult(f"{pm} typecheck", "warn",
                                       f"{n} 项类型告警（仅警告，不中止）"))

    # tsc --noEmit 兜底（有 tsconfig.json 且无 typecheck script, warn-only 静默）
    tsconfig = project_dir / "tsconfig.json"
    if tsconfig.exists() and "typecheck" not in scripts and _have("tsc"):
        if log is not None:
            log("tsc --noEmit（warn-only）")
        p = run(["tsc", "--noEmit"], cwd=str(project_dir), check=False, capture_output=True)
        if p.returncode == 0:
            results.append(CheckResult("tsc --noEmit", "ok"))
        else:
            n = _count_type_errors((p.stdout or "") + (p.stderr or ""))
            results.append(CheckResult("tsc --noEmit", "warn",
                                       f"{n} 项类型告警（仅警告，不中止）"))

    return results


# === 项目类型检测 ===

@dataclass
class ProjectType:
    name: str
    checker: Callable[..., list[CheckResult]]


def _detect_project_types(project_dir: Path) -> list[ProjectType]:
    """检测项目类型（一个项目可命中多种，如 Go + Node 混合）。"""
    types: list[ProjectType] = []

    if (project_dir / "go.mod").exists():
        types.append(ProjectType("Go", _check_go_project))
    if (project_dir / "Cargo.toml").exists():
        types.append(ProjectType("Rust", _check_rust_project))
    if any((project_dir / f).exists() for f in
           ("pyproject.toml", "setup.py", "requirements.txt")):
        types.append(ProjectType("Python", _check_python_project))
    if any((project_dir / f).exists() for f in
           ("build.gradle", "build.gradle.kts", "pom.xml")):
        types.append(ProjectType("Java", _check_java_project))
    if any((project_dir / f).exists() for f in ("Makefile", "CMakeLists.txt")):
        types.append(ProjectType("C/C++", _check_cc_project))
    if (project_dir / "package.json").exists():
        types.append(ProjectType("Node.js", _check_node_project))

    return types


def check_build(*, project_dir: Path = Path("."),
                log: Callable[[str], None] | None = None) -> list[CheckResult]:
    """智能检测项目类型并执行编译检查。

    支持多语言混合项目，返回所有检查点结果。
    失败结果（status=fail）由调用方决定是否抛异常（run_checkwork 汇总后抛）。
    """
    project_dir = project_dir.resolve()
    types = _detect_project_types(project_dir)

    if not types:
        if log is not None:
            log(f"未检测到已知项目类型: {project_dir.name}")
        return []

    parallel = os.environ.get("CHECKWORK_PARALLEL", "") == "1"

    if parallel and len(types) > 1:
        return _run_checks_parallel(types, project_dir, log=log)
    return _run_checks_serial(types, project_dir, log=log)


def _run_checks_serial(types: list[ProjectType], project_dir: Path, *,
                       log: Callable[[str], None] | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    for t in types:
        if log is not None:
            log(f"检测到 {t.name} 项目")
        results.extend(t.checker(project_dir, log=log))
    return results


def _run_checks_parallel(types: list[ProjectType], project_dir: Path, *,
                         log: Callable[[str], None] | None) -> list[CheckResult]:
    """多语言检查点并行（单语言内部仍串行）。并行时 log 输出可能交错。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if log is not None:
        log(f"并行模式: {len(types)} 个语言检查点")

    results: list[CheckResult] = []
    with ThreadPoolExecutor(max_workers=len(types)) as ex:
        futures = {ex.submit(t.checker, project_dir, log=None): t for t in types}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                partial = fut.result()
            except Exception as e:  # noqa: BLE001 — 检查点崩溃不应中断其他语言
                partial = [CheckResult(t.name, "fail", f"检查异常: {e}")]
            results.extend(partial)
    return results


def run_checkwork() -> int:
    """执行编译检查并播报结果。供 bin/checkwork 薄壳调用。"""
    from lib.notify import notify_via_n, project_done_message

    r = reporter(stderr=True)
    r.rule("编译检查", style="blue")
    r.step("开始编译检查...")

    parallel_hint = os.environ.get("CHECKWORK_PARALLEL", "") == "1"
    if not parallel_hint:
        r.info("提示: 设 CHECKWORK_PARALLEL=1 可并行加速多语言检查")

    try:
        results = check_build(project_dir=Path("."), log=r.step)
    except BuildError as e:
        r.err(f"编译失败\n{e}")
        return 2

    if not results:
        r.ok("未检测到已知项目类型，跳过")
        notify_via_n(project_done_message("编译检查完成"))
        return 0

    # 汇总
    _print_results(r, results)

    fails = [x for x in results if x.status == "fail"]
    warns = [x for x in results if x.status == "warn"]

    if fails:
        r.err(f"编译检查失败: {len(fails)} 项")
        notify_via_n(project_done_message("编译检查失败"))
        return 2

    if warns:
        r.warn(f"编译检查通过（{len(warns)} 项告警）")
    else:
        r.ok("编译检查通过")
    notify_via_n(project_done_message("编译检查完成"))
    return 0


def _print_results(r, results: list[CheckResult]) -> None:
    """输出各检查点状态。"""
    for res in results:
        if res.status == "ok":
            r.ok(f"{res.name}")
        elif res.status == "warn":
            msg = f"{res.name} — {res.message}" if res.message else res.name
            r.warn(msg)
        else:
            msg = f"{res.name} — {res.message}" if res.message else res.name
            r.err(msg)
