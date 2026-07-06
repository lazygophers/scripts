#!/usr/bin/env python3
import glob
import os
import shutil
import sys
import time
from dataclasses import dataclass
from typing import List, NoReturn, Optional, Tuple

from .ui import Table, console as rich_console

from .cpd_core import (
    RunCtx,
    Stats,
    copy_file,
    copy_tree,
    count_tree_ops,
    delete_extra_entries,
    ensure_dir,
    fmt_size,
)


def _eprint(*args: object) -> None:
    c = rich_console(stderr=True)
    if c is not None:
        msg = " ".join(str(a) for a in args)
        c.print(msg, markup=False, highlight=False, soft_wrap=True)
        return
    print(*args, file=sys.stderr)


def _usage() -> None:
    _eprint(
        "用法:\n"
        "  cpd [-f] <source> <dest>\n"
        "  cpd [-f] <source1> <source2> ... <dest>\n\n"
        "示例:\n"
        "  cpd ./a.txt ./b.txt\n"
        "  cpd ./dir ./target_dir\n"
        "  cpd \"src/*\" ./backup/\n"
        "  cpd -f src/ ./backup/     # 强制覆盖：让目标目录与 src 内容完全一致（会删除目标多余文件）\n"
        "  cpd -f src/* ./backup/    # 也可：同步某目录下全部内容到目标目录\n"
        "  cpd src/* ./backup/      # 不加引号也可（shell 会先展开成多 source）\n"
        "  CPD_CHECKSUM=0 cpd \"src/*\" ./backup/\n\n"
        "说明:\n"
        "  - 默认只做新增/更新，不做删除（不会删除目标目录里多余的文件）\n"
        "  - -f：强制覆盖（会删除目标目录里 src 不存在的条目；危险操作，建议优先用于目录同步）\n"
        "  - source 以 / 结尾时，目录按“复制目录内容”语义处理（类似 rsync src/ dest/）\n"
        "  - 多 source 匹配时，dest 必须为目录（不存在会自动创建）\n"
        "环境变量:\n"
        "  CPD_CHECKSUM=0   关闭 md5 校验（改用 size+mtime 快速判断）\n"
        "  CPD_VERIFY_MD5=0 关闭复制后的 md5 校验（默认开启）\n"
        "  CPD_INCLUDE_HIDDEN=0  关闭隐藏文件/目录补齐（默认开启）\n"
        "  CPD_LOG=changes|all|quiet  日志级别：仅变更|全量|安静（默认 changes）\n"
    )


def _die(msg: str, code: int = 1) -> NoReturn:
    _eprint(msg)
    raise SystemExit(code)


def _expand_path(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p))


def _env_flag(name: str) -> bool:
    """检查环境变量是否启用（默认启用，设为 0 则禁用）

    支持的环境变量:
        CPD_CHECKSUM: 启用 md5 校验（默认启用）
        CPD_VERIFY_MD5: 启用复制后的 md5 校验（默认启用）
        CPD_INCLUDE_HIDDEN: 启用隐藏文件/目录补齐（默认启用）
    """
    return os.environ.get(name, "1") != "0"


def _log_level() -> str:
    v = (os.environ.get("CPD_LOG") or "changes").strip().lower()
    return v if v in {"all", "changes", "quiet"} else "changes"


def _is_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


class _PlainProgress:
    def __init__(self, total: int) -> None:
        self.total = max(int(total), 1)
        self.completed = 0
        self.copied = 0
        self.skipped = 0
        self.bytes_copied = 0
        self.path = ""
        self._last_len = 0
        self._last_render = 0.0

    def clear(self) -> None:
        if self._last_len <= 0:
            return
        sys.stderr.write("\r" + (" " * self._last_len) + "\r")
        sys.stderr.flush()
        self._last_len = 0

    def render(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and (now - self._last_render) < 0.05:
            return
        self._last_render = now

        try:
            width = shutil.get_terminal_size((120, 20)).columns
        except Exception:
            width = 120

        bar_width = max(10, min(30, width // 4))
        ratio = min(max(self.completed / self.total, 0.0), 1.0)
        filled = int(ratio * bar_width)
        bar = "█" * filled + " " * (bar_width - filled)

        left = (
            f"[{bar}] {self.completed}/{self.total} "
            f"已复制{self.copied} 已跳过{self.skipped} 已传输{fmt_size(self.bytes_copied)}"
        )
        msg = f"{left}  当前 {self.path}" if self.path else left

        if len(msg) > width - 1:
            msg = msg[: max(0, width - 2)] + "…"

        self.clear()
        sys.stderr.write(msg)
        sys.stderr.flush()
        self._last_len = len(msg)

    def update_counts(self, *, copied: int, skipped: int, bytes_copied: int) -> None:
        self.copied = copied
        self.skipped = skipped
        self.bytes_copied = bytes_copied

    def advance(self, *, path: str) -> None:
        self.completed += 1
        self.path = path
        self.render()

    def finish(self) -> None:
        self.render(force=True)
        self.clear()


@dataclass(frozen=True)
class CopyPlan:
    sources: List[Tuple[str, bool]]
    dest: str
    dest_force_dir: bool
    force: bool


def _strip_trailing_sep(p: str) -> str:
    if p == os.sep:
        return p
    return p.rstrip(os.sep)


def _glob_with_hidden(pattern: str) -> List[str]:
    matches = glob.glob(pattern)
    dirpart, base = os.path.split(pattern)
    if base and not base.startswith(".") and glob.has_magic(base) and base != "**":
        matches.extend(glob.glob(os.path.join(dirpart, f".{base}")))
    seen: set[str] = set()
    return [
        m for m in matches
        if os.path.basename(m) not in {".", ".."} and m not in seen and not seen.add(m)  # type: ignore[func-returns-value]
    ]


def _resolve_sources(raw_sources: List[str]) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    include_hidden = _env_flag("CPD_INCLUDE_HIDDEN")
    for raw in raw_sources:
        copy_contents = raw.endswith(os.sep)
        expanded = _expand_path(raw)
        expanded_for_glob = _strip_trailing_sep(expanded) if copy_contents else expanded

        if glob.has_magic(expanded_for_glob):
            matches = _glob_with_hidden(expanded_for_glob) if include_hidden else glob.glob(expanded_for_glob)
            if not matches and not include_hidden:
                matches = glob.glob(expanded_for_glob, recursive=True)
            if not matches:
                _die(f"未匹配到任何源路径: {raw}")
            out.extend((m, copy_contents) for m in matches)
            continue

        literal = _strip_trailing_sep(expanded) if copy_contents else expanded
        if not os.path.lexists(literal):
            _die(f"源路径不存在: {raw}")
        out.append((literal, copy_contents))
    return out


def _augment_hidden_entries(sources: List[Tuple[str, bool]]) -> None:
    """补齐 shell * 展开遗漏的隐藏条目"""
    parents = {os.path.dirname(os.path.normpath(s)) for s, _ in sources}
    if len(parents) != 1:
        return
    parent = next(iter(parents))
    try:
        all_names = os.listdir(parent)
    except FileNotFoundError:
        return
    visible = sorted(n for n in all_names if not n.startswith(".") and n not in {".", ".."})
    provided = sorted(os.path.basename(os.path.normpath(s)) for s, _ in sources)
    if not visible or provided != visible:
        return
    existing = {os.path.normpath(s) for s, _ in sources}
    for name in all_names:
        if not name.startswith(".") or name in {".", ".."}:
            continue
        full = os.path.join(parent, name)
        if os.path.normpath(full) not in existing:
            sources.append((full, False))


def _resolve_plan(raw_sources: List[str], raw_dst: str, *, force: bool) -> CopyPlan:
    dst = _expand_path(raw_dst)
    sources = _resolve_sources(raw_sources)
    dest_force_dir = raw_dst.endswith(os.sep)

    # 处理”shell 已经展开的 *”场景：补齐隐藏条目
    if _env_flag("CPD_INCLUDE_HIDDEN") and len(raw_sources) > 1:
        if not any(glob.has_magic(_strip_trailing_sep(_expand_path(r))) for r in raw_sources):
            _augment_hidden_entries(sources)

    return CopyPlan(sources=sources, dest=dst, dest_force_dir=dest_force_dir, force=force)


def _parse_cli(argv: list[str]) -> Tuple[bool, List[str], str]:
    args = argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        _usage()
        raise SystemExit(0 if args else 2)

    force = args[0] == "-f"
    if force:
        args = args[1:]

    if len(args) < 2:
        _usage()
        raise SystemExit(2)

    return force, args[:-1], args[-1]


def _compute_single_dir_dst_root(
    *,
    src_dir: str,
    dst: str,
    dest_force_dir: bool,
    copy_dir_contents: bool,
    dst_is_dir_before: bool,
) -> str:
    if (dest_force_dir or dst_is_dir_before) and not copy_dir_contents:
        return os.path.join(dst, os.path.basename(os.path.normpath(src_dir)))
    return dst


def _entries_set(dir_path: str, *, include_hidden: bool) -> set[str]:
    try:
        names = os.listdir(dir_path)
    except FileNotFoundError:
        return set()
    out = set()
    for n in names:
        if n in {".", ".."}:
            continue
        if not include_hidden and n.startswith("."):
            continue
        out.add(n)
    return out


def _validate_force_multi_sources(*, sources: List[Tuple[str, bool]], include_hidden: bool) -> str:
    if not sources:
        _die("-f 模式下未提供源路径")

    if any(copy_contents for _src, copy_contents in sources):
        _die("-f + 多源模式不支持以 / 结尾的 source（请改用：cpd -f src/ dest/）")

    parents = {os.path.dirname(os.path.normpath(s)) for s, _copy_contents in sources}
    if len(parents) != 1:
        _die("-f + 多源模式要求所有 source 来自同一父目录（例如：cpd -f src/* dest/）")
    src_root = next(iter(parents))

    provided_names = {os.path.basename(os.path.normpath(s)) for s, _copy_contents in sources}
    expected_names = _entries_set(src_root, include_hidden=include_hidden)

    if not expected_names:
        _die(f"-f 无法读取源目录内容: {src_root}")

    if provided_names != expected_names:
        _die(
            "-f + 多源模式仅支持“全量同步”语义（例如：cpd -f src/* dest/ 或 cpd -f src/ dest/）。"
            "当前 source 看起来是子集，为避免误删已拒绝执行。"
        )

    return src_root


def _copy_single(src: str, dst: str, dest_force_dir: bool, copy_dir_contents: bool, ctx: RunCtx) -> None:
    src_is_dir = os.path.isdir(src) and not os.path.islink(src)
    treat_dst_as_dir = dest_force_dir or os.path.isdir(dst)

    if treat_dst_as_dir:
        ensure_dir(dst)
        if src_is_dir:
            target = dst if copy_dir_contents else os.path.join(dst, os.path.basename(os.path.normpath(src)))
            copy_tree(src, target, ctx)
        else:
            copy_file(src, os.path.join(dst, os.path.basename(src)), ctx)
        return

    if src_is_dir:
        if os.path.lexists(dst) and not os.path.isdir(dst):
            _die(f"源是目录，但目标不是目录: {dst}")
        copy_tree(src, dst, ctx)
        return

    copy_file(src, dst, ctx)


def _estimate_total_ops(plan: CopyPlan) -> int:
    total = 0
    for src, _copy_contents in plan.sources:
        if os.path.isdir(src) and not os.path.islink(src):
            total += count_tree_ops(src)
        else:
            total += 1
    return max(total, 1)


def _validate_force_mode(plan: CopyPlan, raw_dst: str) -> Optional[str]:
    """验证并返回 -f 模式下的多源根目录（如有）"""
    if not plan.force:
        return None

    include_hidden = _env_flag("CPD_INCLUDE_HIDDEN")
    if len(plan.sources) == 1:
        force_src, _force_copy_contents = plan.sources[0]
        if not (os.path.isdir(force_src) and not os.path.islink(force_src)):
            _die("-f 仅支持目录同步（source 必须是目录且不能是软链接）")
        return None

    if os.path.lexists(plan.dest) and not os.path.isdir(plan.dest):
        _die(f"-f 多源同步时目标必须为目录: {raw_dst}")
    return _validate_force_multi_sources(sources=plan.sources, include_hidden=include_hidden)


def _prepare_context(plan: CopyPlan) -> RunCtx:
    """准备运行上下文，包括进度条和统计信息"""
    checksum = _env_flag("CPD_CHECKSUM")
    verify_md5 = _env_flag("CPD_VERIFY_MD5")
    log = _log_level()

    tty = _is_tty()
    console = rich_console(stderr=True) if tty else None
    progress: Optional[object] = None
    task_id: Optional[int] = None
    plain_progress: Optional[_PlainProgress] = None

    dest_is_dir_root = plan.dest_force_dir or len(plan.sources) > 1 or os.path.isdir(plan.dest)
    if not dest_is_dir_root and len(plan.sources) == 1:
        src, cc = plan.sources[0]
        dest_is_dir_root = cc or (os.path.isdir(src) and not os.path.islink(src))

    display_base = plan.dest if dest_is_dir_root else (os.path.dirname(plan.dest) or ".")

    if console is not None and Table is not None:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

        total_ops = _estimate_total_ops(plan)
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]复制[/bold]"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("已复制 {task.fields[copied]}"),
            TextColumn("已跳过 {task.fields[skipped]}"),
            TextColumn("已传输 {task.fields[bytes]}"),
            TimeElapsedColumn(),
            TextColumn("{task.fields[path]}", markup=False),
            console=console,
            transient=False,
        )
        task_id = progress.add_task("", total=total_ops, copied=0, skipped=0, bytes="0B", path="")
    elif tty:
        plain_progress = _PlainProgress(total=_estimate_total_ops(plan))
        plain_progress.render(force=True)

    return RunCtx(
        checksum=checksum,
        verify_md5=verify_md5,
        log=log,
        display_base=display_base,
        stats=Stats(),
        console=console,
        progress=progress,
        task_id=task_id,
        plain_progress=plain_progress,
    )


def _print_copy_plan(plan: CopyPlan, ctx: RunCtx) -> None:
    """打印复制计划和源清单"""
    log_labels = {"all": "全量", "changes": "仅变更", "quiet": "安静"}
    on_off = lambda v: "开启" if v else "关闭"
    rows = [
        ("目标", str(plan.dest)),
        ("源数量", str(len(plan.sources))),
        ("强制覆盖(-f)", on_off(plan.force)),
        ("校验(对比)", on_off(ctx.checksum)),
        ("复制后MD5校验", on_off(ctx.verify_md5)),
        ("日志", log_labels.get(ctx.log, ctx.log)),
    ]

    console = ctx.console
    if console is not None and Table is not None:
        table = Table(title="复制计划", show_header=False, box=None)
        for k, v in rows:
            table.add_row(k, v)
        console.print(table)  # type: ignore[attr-defined]

        sources_table = Table(title="源清单", show_header=True, box=None)
        sources_table.add_column("源路径", overflow="fold")
        sources_table.add_column("模式", width=12)
        for src, copy_contents in plan.sources:
            sources_table.add_row(f"{src}{os.sep if copy_contents else ''}", "目录内容" if copy_contents else "路径")
        console.print(sources_table)  # type: ignore[attr-defined]
    else:
        ctx.print_line("复制计划")
        for k, v in rows:
            ctx.print_line(f"{k}: {v}")
        for src, copy_contents in plan.sources:
            ctx.print_line(f"源: {src}{os.sep if copy_contents else ''}")


def _execute_multi_source_copy(
    plan: CopyPlan,
    raw_dst: str,
    force_multi_src_root: Optional[str],
    ctx: RunCtx,
) -> None:
    """执行多源复制"""
    if os.path.lexists(plan.dest) and not os.path.isdir(plan.dest):
        _die(f"多源复制时目标必须为目录: {raw_dst}")
    ensure_dir(plan.dest)

    for src, copy_contents in plan.sources:
        _copy_single(src, plan.dest, True, copy_contents, ctx)

    if plan.force:
        if force_multi_src_root is None:
            _die("-f 内部错误：未计算多源同步根目录")
        ctx.print_line("开始清理目标多余条目（-f）")
        delete_extra_entries(
            src_root=force_multi_src_root,
            dst_root=plan.dest,
            include_hidden=_env_flag("CPD_INCLUDE_HIDDEN"),
            ctx=ctx,
        )


def _execute_single_source_copy(plan: CopyPlan, ctx: RunCtx) -> None:
    """执行单源复制"""
    single_src, single_copy_contents = plan.sources[0]
    if single_copy_contents and not (os.path.isdir(single_src) and not os.path.islink(single_src)):
        _die("source 以 / 结尾时必须是目录")

    dst_is_dir_before = os.path.isdir(plan.dest)
    _copy_single(single_src, plan.dest, plan.dest_force_dir, single_copy_contents, ctx)

    if plan.force:
        dst_root = _compute_single_dir_dst_root(
            src_dir=single_src,
            dst=plan.dest,
            dest_force_dir=plan.dest_force_dir,
            copy_dir_contents=single_copy_contents,
            dst_is_dir_before=dst_is_dir_before,
        )
        ctx.print_line("开始清理目标多余条目（-f）")
        delete_extra_entries(
            src_root=single_src,
            dst_root=dst_root,
            include_hidden=_env_flag("CPD_INCLUDE_HIDDEN"),
            ctx=ctx,
        )


def _execute_copy(
    plan: CopyPlan,
    raw_dst: str,
    force_multi_src_root: Optional[str],
    ctx: RunCtx,
) -> None:
    """执行复制操作"""
    if len(plan.sources) > 1:
        _execute_multi_source_copy(plan, raw_dst, force_multi_src_root, ctx)
    else:
        _execute_single_source_copy(plan, ctx)


def _print_summary(plan: CopyPlan, ctx: RunCtx, elapsed: float) -> None:
    """打印复制完成摘要"""
    if ctx.progress is not None and ctx.task_id is not None:
        try:
            ctx.progress.update(  # type: ignore[union-attr,attr-defined]
                ctx.task_id,
                bytes=fmt_size(ctx.stats.copied_bytes),
                path="",
            )
        except Exception:
            pass
    if ctx.plain_progress is not None:
        ctx.plain_progress.finish()  # type: ignore[attr-defined]

    s = ctx.stats
    rows = [
        ("目标", str(plan.dest)),
        ("已复制文件", str(s.copied_files)),
        ("已跳过文件", str(s.skipped_files)),
        ("已创建目录", str(s.created_dirs)),
        ("已跳过目录", str(s.skipped_dirs)),
        ("已同步软链", str(s.synced_links)),
        ("已跳过软链", str(s.skipped_links)),
        ("已删除文件", str(s.deleted_files)),
        ("已删除目录", str(s.deleted_dirs)),
        ("已删除软链", str(s.deleted_links)),
        ("已处理条目", str(s.processed_entries)),
        ("已传输", fmt_size(s.copied_bytes)),
        ("耗时", f"{elapsed:.2f}s"),
    ]

    console = ctx.console
    if console is not None and Table is not None:
        table = Table(title="复制完成", show_header=False, box=None)
        for k, v in rows:
            table.add_row(k, v)
        console.print(table)  # type: ignore[attr-defined]
    else:
        _eprint("复制完成")
        for k, v in rows:
            _eprint(f"{k}: {v}")


def copy(argv: list[str]) -> int:
    force, raw_sources, raw_dst = _parse_cli(argv)
    plan = _resolve_plan(raw_sources, raw_dst, force=force)
    force_multi_src_root = _validate_force_mode(plan, raw_dst)

    ctx = _prepare_context(plan)
    _print_copy_plan(plan, ctx)

    start = time.perf_counter()
    if ctx.progress is not None:
        with ctx.progress:  # type: ignore[attr-defined]
            _execute_copy(plan, raw_dst, force_multi_src_root, ctx)
    else:
        _execute_copy(plan, raw_dst, force_multi_src_root, ctx)

    elapsed = time.perf_counter() - start
    _print_summary(plan, ctx, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(copy(sys.argv))
