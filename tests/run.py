#!/usr/bin/env python3
"""并行测试运行器：每个测试模块一个子进程，按 CPU 数并发。

`python3 -m unittest discover -s tests -q` 串行跑完整套实测 838s，其中 CPU
只占 131s——大头是等 subprocess 和 sleep。模块之间本来就互不依赖，这里按
「模块」或「模块.测试类」切成调度单元并发跑，实测同机 55s。

用法：
    python3 tests/run.py                 # 全量并行
    python3 tests/run.py -j 4            # 指定并发数
    python3 tests/run.py test_git_core   # 只跑指定模块
    python3 tests/run.py --timings       # 额外打印每个模块耗时（降序）

隔离：每个 worker 拿独立的 TMPDIR 和 SCRIPTS_LOG，避免 daemon socket、
锁文件、日志文件互相踩。BROWSE_BRIDGE_PORT=0 让 bridge 用随机端口。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


CLASS_RE = re.compile(r"^class (\w+)\(([^)]*)\)\s*:", re.M)
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
# 少于这个数的模块整体调度：拆得太碎，每个子进程要重付一次解释器 + import 的钱
SPLIT_MIN_CLASSES = 4


def testcase_classes(src: str) -> list[str]:
    """文件里所有（间接）继承 unittest.TestCase 的顶层类。

    辅助基类（FakeArchery(BaseHTTPRequestHandler) 之类）必须排除：
    `unittest <模块>.<类>` 会直接实例化它们，构造签名不匹配就整单元报错。
    """
    declared = CLASS_RE.findall(src)
    names = {c: [b.strip() for b in bases.split(",")] for c, bases in declared}
    cases: list[str] = []
    for cls, _ in declared:
        seen, stack = set(), list(names[cls])
        while stack:
            base = stack.pop()
            if base in seen:
                continue
            seen.add(base)
            if base.endswith("TestCase"):
                cases.append(cls)
                break
            stack.extend(names.get(base.rsplit(".", 1)[-1], []))
    return cases


def discover() -> list[str]:
    return sorted(p.stem for p in TESTS.glob("test_*.py"))


def split_units(modules: list[str]) -> list[str]:
    """把模块切成「模块.类」粒度的调度单元。

    模块粒度下最慢的单个模块就是整套的下界（test_squash_pr 一个人跑 61s）。
    按顶层 TestCase 类切开后，同一模块的类能分散到多个 worker。
    正则找顶层 class 即可，不在父进程 import 测试模块（import 本身有副作用）。
    """
    units: list[str] = []
    for mod in modules:
        if "." in mod:  # 已经是 模块.类，直接用
            units.append(mod)
            continue
        src = (TESTS / f"{mod}.py").read_text(encoding="utf-8", errors="ignore")
        classes = testcase_classes(src)
        if len(classes) >= SPLIT_MIN_CLASSES:
            units.extend(f"{mod}.{c}" for c in classes)
        else:
            units.append(mod)
    return units


def run_module(name: str) -> tuple[str, int, float, str]:
    """跑单个调度单元（模块或 模块.类），返回 (名字, 退出码, 耗时秒, 输出)。"""
    env = dict(os.environ)
    # 路径必须短：browse daemon 的 unix socket 全路径在 macOS 上限 104 字节，
    # 系统 TMPDIR（/var/folders/…）再套一层 mkdtemp 就会超。
    tmp = tempfile.mkdtemp(prefix="lg", dir="/tmp")
    env["TMPDIR"] = tmp
    env["SCRIPTS_LOG"] = os.path.join(tmp, "scripts.log")
    env.setdefault("BROWSE_BRIDGE_PORT", "0")
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", f"tests.{name}"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - start
    shutil.rmtree(tmp, ignore_errors=True)  # 511 个单元 = 511 个临时目录，跑完就收
    out = proc.stdout + proc.stderr
    code = proc.returncode
    # 类粒度调度会撞上辅助基类（没有 test_ 方法），unittest 对此报 NO TESTS RAN
    # 并退出 1。「某个模块整体一个测试都没收集到」由 test_meta.py 单独守着。
    if code != 0 and "NO TESTS RAN" in out:
        code = 0
    return name, code, elapsed, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="并行跑 tests/ 下的测试模块")
    ap.add_argument("modules", nargs="*", help="只跑这些模块（默认全量）")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--timings", action="store_true", help="打印每模块耗时")
    args = ap.parse_args(argv)

    modules = split_units(args.modules or discover())
    started = time.perf_counter()
    results: list[tuple[str, int, float, str]] = []

    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_module, m): m for m in modules}
        for fut in as_completed(futures):
            name, code, secs, out = fut.result()
            results.append((name, code, secs, out))
            print(("." if code == 0 else "F"), end="", flush=True)
    print()

    failed = [r for r in results if r[1] != 0]
    for name, _code, _secs, out in failed:
        print(f"\n===== FAILED: {name} =====\n{out.strip()}")

    if args.timings:
        print("\n耗时（秒，降序）:")
        for name, _code, secs, _out in sorted(results, key=lambda r: -r[2]):
            print(f"  {secs:7.2f}  {name}")

    total = time.perf_counter() - started
    ran = sum(int(m.group(1)) for _n, _c, _s, out in results
              for m in RAN_RE.finditer(out))
    print(f"\n{ran} 用例 / {len(results)} 单元, {len(failed)} 失败, "
          f"墙钟 {total:.1f}s, 并发 {args.jobs}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
