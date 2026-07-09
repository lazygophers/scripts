#!/usr/bin/env python3
"""cpd 核心复制引擎模块

提供文件/目录复制、校验、同步等核心功能。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass


@dataclass
class Stats:
    """复制统计数据"""

    processed_entries: int = 0
    processed_dirs: int = 0
    processed_files: int = 0
    processed_links: int = 0
    deleted_dirs: int = 0
    deleted_files: int = 0
    deleted_links: int = 0
    copied_files: int = 0
    skipped_files: int = 0
    created_dirs: int = 0
    skipped_dirs: int = 0
    synced_links: int = 0
    skipped_links: int = 0
    copied_bytes: int = 0


@dataclass
class RunCtx:
    """运行上下文：包含配置、统计、进度追踪等"""

    checksum: bool
    verify_md5: bool
    log: str
    display_base: str
    stats: Stats
    console: object | None
    progress: object | None
    task_id: int | None
    plain_progress: object | None

    def _rich_enabled(self) -> bool:
        return self.console is not None or self.progress is not None

    def _dst_rel(self, dst: str) -> str:
        try:
            return os.path.relpath(dst, self.display_base) or "."
        except Exception:
            return dst

    def _should_log(self, status: str) -> bool:
        return self.log == "all" or (self.log != "quiet" and status != "已跳过")

    def print_line(self, msg: object) -> None:
        import sys

        if self.plain_progress is not None:
            self.plain_progress.clear()  # type: ignore[attr-defined]
        rich_out = self.progress.console if self.progress is not None else self.console  # type: ignore[union-attr,attr-defined]
        if rich_out is not None:
            rich_out.print(msg, markup=False, highlight=False, soft_wrap=True)  # type: ignore[union-attr,attr-defined]
            return
        print(str(msg), file=sys.stderr)
        if self.plain_progress is not None:
            self.plain_progress.render(force=True)  # type: ignore[attr-defined]

    def report(self, status: str, kind: str, dst: str, *, size_bytes: int | None = None, extra: str = "") -> None:
        if not self._should_log(status):
            return

        rel = self._dst_rel(dst)
        size_part = f" 大小{fmt_size(size_bytes)}" if size_bytes is not None else ""
        suffix = f" {extra}" if extra else ""

        if self._rich_enabled():
            from rich.text import Text

            _STATUS_STYLE = {"已复制": "bold green", "已创建": "bold green", "已同步": "bold green",
                             "已删除": "bold red", "已跳过": "bold yellow"}
            _KIND_STYLE = {"文件": "cyan", "目录": "blue", "软链接": "magenta"}
            line = Text()
            line.append(status, style=_STATUS_STYLE.get(status))
            line.append(" ")
            line.append(kind, style=_KIND_STYLE.get(kind))
            line.append(f" {rel}")
            for part in (size_part, suffix):
                if part:
                    line.append(part, style="dim")
            self.print_line(line)
            return

        self.print_line(f"{status} {kind} {rel}{size_part}{suffix}")

    def advance(self, path: str) -> None:
        self.stats.processed_entries += 1
        if self.progress is not None and self.task_id is not None:
            self.progress.update(  # type: ignore[union-attr,attr-defined]
                self.task_id,
                advance=1,
                path=path,
                copied=self.stats.copied_files,
                skipped=self.stats.skipped_files,
                bytes=fmt_size(self.stats.copied_bytes),
            )
        if self.plain_progress is not None:
            self.plain_progress.update_counts(  # type: ignore[attr-defined]
                copied=self.stats.copied_files,
                skipped=self.stats.skipped_files,
                bytes_copied=self.stats.copied_bytes,
            )
            self.plain_progress.advance(path=path)  # type: ignore[attr-defined]


def fmt_size(num_bytes: int | None) -> str:
    """格式化字节大小为人类可读格式"""
    if num_bytes is None or num_bytes < 0:
        return "0B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(num_bytes)
    unit_idx = 0
    while size >= 1024.0 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(size)}{units[unit_idx]}"
    return f"{size:.1f}{units[unit_idx]}"


def md5_file(path: str) -> str:
    """计算文件的 MD5 哈希值。

    使用流式读取（每次 1MB）以支持大文件。

    Args:
        path: 文件路径

    Returns:
        32 位十六进制 MD5 哈希值
    """
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def should_copy_file(src: str, dst: str, checksum: bool) -> bool:
    """判断文件是否需要复制（通过大小、时间戳或 MD5 判断）"""
    try:
        dst_stat = os.stat(dst, follow_symlinks=False)
    except FileNotFoundError:
        return True

    if not os.path.isfile(dst) and not os.path.islink(dst):
        return True

    src_stat = os.stat(src, follow_symlinks=False)
    if src_stat.st_size != dst_stat.st_size:
        return True

    if not checksum:
        return src_stat.st_mtime_ns != dst_stat.st_mtime_ns

    if src_stat.st_mtime_ns == dst_stat.st_mtime_ns:
        return False

    if os.path.islink(src) or os.path.islink(dst):
        return os.path.islink(src) != os.path.islink(dst) or os.readlink(src) != os.readlink(dst)

    return md5_file(src) != md5_file(dst)


def _verify_fail(msg: str) -> None:
    """校验失败时输出错误信息并退出"""
    import sys
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def verify_symlink_equal(src: str, dst: str) -> str:
    """校验符号链接复制后是否一致"""
    expected = os.readlink(src)
    if not os.path.islink(dst):
        _verify_fail(f"复制后校验失败：目标不是符号链接: {dst}")
    actual = os.readlink(dst)
    if actual != expected:
        _verify_fail(f"复制后校验失败：符号链接目标不一致: {src} -> {dst} expected={expected} actual={actual}")
    return expected


def verify_file_md5_equal(src: str, dst: str) -> tuple[str, str]:
    """校验文件复制后 MD5 是否一致"""
    src_md5 = md5_file(src)
    dst_md5 = md5_file(dst)
    if src_md5 != dst_md5:
        _verify_fail(f"复制后 md5 校验失败: {src} -> {dst} src_md5={src_md5} dst_md5={dst_md5}")
    return src_md5, dst_md5


def remove_any(path: str) -> None:
    """删除任意类型的文件系统条目（文件、目录、符号链接）"""
    if not os.path.lexists(path):
        return
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def ensure_dir(path: str) -> None:
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def copy_symlink(src: str, dst: str) -> None:
    """复制符号链接"""
    target = os.readlink(src)
    if os.path.lexists(dst):
        remove_any(dst)
    ensure_dir(os.path.dirname(dst) or ".")
    os.symlink(target, dst)


def should_copy_symlink(src: str, dst: str) -> bool:
    """判断符号链接是否需要复制"""
    target = os.readlink(src)
    if not os.path.islink(dst):
        return True
    try:
        return os.readlink(dst) != target
    except OSError:
        return True


def _handle_symlink(src: str, dst: str, ctx: RunCtx) -> None:
    """处理符号链接的复制/跳过逻辑（统一入口）"""
    target = os.readlink(src)
    ctx.stats.processed_links += 1
    if should_copy_symlink(src, dst):
        copy_symlink(src, dst)
        if ctx.verify_md5:
            verify_symlink_equal(src, dst)
        ctx.stats.synced_links += 1
        ctx.report("已同步", "软链接", dst, extra=f"=> {target}")
    else:
        ctx.stats.skipped_links += 1
        ctx.report("已跳过", "软链接", dst, extra=f"=> {target}")
    ctx.advance(ctx._dst_rel(dst))


def ensure_dir_for_copy(dst_dir: str, *, src_dir: str | None, ctx: RunCtx) -> bool:
    """确保目标目录存在，并记录统计信息"""
    already_exists = os.path.isdir(dst_dir) and not os.path.islink(dst_dir)
    if not already_exists:
        remove_any(dst_dir)
        os.makedirs(dst_dir, exist_ok=True)

    ctx.stats.processed_dirs += 1
    if already_exists:
        ctx.stats.skipped_dirs += 1
        ctx.report("已跳过", "目录", dst_dir)
    else:
        ctx.stats.created_dirs += 1
        if src_dir is not None:
            try:
                shutil.copystat(src_dir, dst_dir, follow_symlinks=False)
            except OSError:
                try:
                    os.chmod(dst_dir, os.stat(src_dir, follow_symlinks=False).st_mode)
                except OSError:
                    pass
        ctx.report("已创建", "目录", dst_dir)
    ctx.advance(ctx._dst_rel(dst_dir))
    return not already_exists


def copy_file(src: str, dst: str, ctx: RunCtx) -> None:
    """复制文件或符号链接（底层函数）"""
    if os.path.islink(src):
        _handle_symlink(src, dst, ctx)
        return

    if os.path.islink(dst) or (os.path.lexists(dst) and os.path.isdir(dst)):
        remove_any(dst)
    ensure_dir(os.path.dirname(dst) or ".")

    ctx.stats.processed_files += 1
    size_bytes = int(os.stat(src, follow_symlinks=False).st_size)
    if should_copy_file(src, dst, ctx.checksum):
        shutil.copy2(src, dst)
        # verify_md5 只在校验且实际复制了文件时才计算
        # （should_copy_file 返回 True 时，MD5 已确认不同，无需重复校验）
        ctx.stats.copied_files += 1
        ctx.stats.copied_bytes += size_bytes
        ctx.report("已复制", "文件", dst, size_bytes=size_bytes)
    else:
        ctx.stats.skipped_files += 1
        ctx.report("已跳过", "文件", dst, size_bytes=size_bytes)
    ctx.advance(ctx._dst_rel(dst))


def _process_walk_dirs(dirnames: list[str], root: str, dst_root: str, ctx: RunCtx) -> list[str]:
    """处理 walk 遍历中的目录列表，将符号链接目录单独处理"""
    real_dirs: list[str] = []
    for d in dirnames:
        src_path = os.path.join(root, d)
        dst_path = os.path.join(dst_root, d)
        if os.path.islink(src_path):
            _handle_symlink(src_path, dst_path, ctx)
            continue
        real_dirs.append(d)
    return real_dirs


def _process_walk_files(filenames: list[str], root: str, dst_root: str, ctx: RunCtx) -> None:
    """处理 walk 遍历中的文件列表"""
    for f in filenames:
        src_path = os.path.join(root, f)
        dst_path = os.path.join(dst_root, f)
        copy_file(src_path, dst_path, ctx)


def copy_tree(src_dir: str, dst_dir: str, ctx: RunCtx) -> None:
    """递归复制目录树（核心复制引擎）。

    支持智能跳过（基于时间戳/大小/MD5）、符号链接处理、进度跟踪。

    Args:
        src_dir: 源目录路径
        dst_dir: 目标目录路径
        ctx: 运行上下文（包含配置、统计、进度等）
    """
    if os.path.islink(src_dir):
        _handle_symlink(src_dir, dst_dir, ctx)
        return

    if os.path.lexists(dst_dir) and (os.path.islink(dst_dir) or not os.path.isdir(dst_dir)):
        remove_any(dst_dir)
    ensure_dir_for_copy(dst_dir, src_dir=src_dir, ctx=ctx)

    for root, dirnames, filenames in os.walk(src_dir, topdown=True, followlinks=False):
        rel = os.path.relpath(root, src_dir)
        dst_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        if rel != ".":
            ensure_dir_for_copy(dst_root, src_dir=root, ctx=ctx)
        dirnames[:] = _process_walk_dirs(dirnames, root, dst_root, ctx)
        _process_walk_files(filenames, root, dst_root, ctx)


def _delete_and_report_entry(entry: os.DirEntry, ctx: RunCtx) -> None:  # type: ignore[type-arg]
    """删除单个条目并按类型更新统计"""
    dst_path = entry.path
    remove_any(dst_path)
    if entry.is_symlink():
        ctx.stats.deleted_links += 1
        kind = "软链接"
    elif entry.is_dir(follow_symlinks=False):
        ctx.stats.deleted_dirs += 1
        kind = "目录"
    else:
        ctx.stats.deleted_files += 1
        kind = "文件"
    ctx.report("已删除", kind, dst_path)
    ctx.advance(ctx._dst_rel(dst_path))


def _classify_and_delete_entry(
    entry: os.DirEntry, src_root: str, dst_root: str, include_hidden: bool, ctx: RunCtx  # type: ignore[type-arg]
) -> None:
    """对单个目标条目进行分类：跳过、删除或递归处理"""
    name = entry.name
    if name in {".", ".."} or (not include_hidden and name.startswith(".")):
        return

    src_path = os.path.join(src_root, os.path.relpath(entry.path, dst_root))
    if not os.path.lexists(src_path):
        _delete_and_report_entry(entry, ctx)
        return

    if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
        delete_extra_entries(src_root=src_path, dst_root=entry.path, include_hidden=include_hidden, ctx=ctx)


def delete_extra_entries(*, src_root: str, dst_root: str, include_hidden: bool, ctx: RunCtx) -> None:
    """删除目标目录中源目录不存在的条目（-f 模式）。

    递归遍历目标目录，删除源目录中不存在的文件/目录/符号链接。

    Args:
        src_root: 源根目录
        dst_root: 目标根目录
        include_hidden: 是否处理隐藏文件（以 . 开头）
        ctx: 运行上下文（包含统计和进度）
    """
    if not os.path.isdir(dst_root) or os.path.islink(dst_root):
        print(f"-f 模式下目标必须为真实目录（不能是软链接）: {dst_root}", file=__import__("sys").stderr)
        raise SystemExit(1)

    try:
        entries = list(os.scandir(dst_root))
    except FileNotFoundError:
        return

    for entry in entries:
        _classify_and_delete_entry(entry, src_root, dst_root, include_hidden, ctx)


def count_tree_ops(src_dir: str) -> int:
    """预估复制目录树需要的操作数（用于进度显示）"""
    if os.path.islink(src_dir):
        return 1
    if not (os.path.isdir(src_dir) and os.path.lexists(src_dir)):
        return 1

    ops = 1
    for root, dirnames, filenames in os.walk(src_dir, topdown=True, followlinks=False):
        rel = os.path.relpath(root, src_dir)
        if rel != ".":
            ops += 1

        new_dirnames = []
        for d in dirnames:
            src_path = os.path.join(root, d)
            if os.path.islink(src_path):
                ops += 1
                continue
            new_dirnames.append(d)
        dirnames[:] = new_dirnames
        ops += len(filenames)
    return ops
