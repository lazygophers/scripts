#!/usr/bin/env python3
"""cpd_core 边界分支测试：软链接、校验失败、删除分类、进度回调。"""
import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import lib.cpd_core as core
from lib.cpd_core import (
    RunCtx,
    Stats,
    copy_file,
    copy_tree,
    count_tree_ops,
    delete_extra_entries,
    ensure_dir_for_copy,
    should_copy_file,
    should_copy_symlink,
    verify_file_md5_equal,
    verify_symlink_equal,
)


def _ctx(base, *, log="quiet", verify_md5=False, checksum=True,
         console=None, progress=None, task_id=None, plain_progress=None):
    return RunCtx(
        checksum=checksum,
        verify_md5=verify_md5,
        log=log,
        display_base=base,
        stats=Stats(),
        console=console,
        progress=progress,
        task_id=task_id,
        plain_progress=plain_progress,
    )


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _file(self, name, content="x"):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        return p


class TestRunCtxOutput(_TmpCase):
    def test_dst_rel_falls_back_on_error(self):
        ctx = _ctx(self.d)
        with patch("os.path.relpath", side_effect=ValueError):
            self.assertEqual(ctx._dst_rel("/a/b"), "/a/b")

    def test_print_line_to_stderr_without_rich(self):
        ctx = _ctx(self.d)
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ctx.print_line("hello")
        self.assertEqual(buf.getvalue().strip(), "hello")

    def test_print_line_clears_and_rerenders_plain_progress(self):
        pp = MagicMock()
        ctx = _ctx(self.d, plain_progress=pp)
        with patch("sys.stderr", io.StringIO()):
            ctx.print_line("hello")
        pp.clear.assert_called_once()
        pp.render.assert_called_once_with(force=True)

    def test_print_line_prefers_progress_console(self):
        con = MagicMock()
        prog = MagicMock(console=con)
        ctx = _ctx(self.d, progress=prog)
        ctx.print_line("hi")
        con.print.assert_called_once()

    def test_report_quiet_suppresses(self):
        ctx = _ctx(self.d, log="quiet")
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ctx.report("已复制", "文件", os.path.join(self.d, "a"))
        self.assertEqual(buf.getvalue(), "")

    def test_report_default_hides_skips_only(self):
        ctx = _ctx(self.d, log="normal")
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ctx.report("已跳过", "文件", os.path.join(self.d, "a"))
            ctx.report("已复制", "文件", os.path.join(self.d, "b"), size_bytes=1024)
        out = buf.getvalue()
        self.assertNotIn("已跳过", out)
        self.assertIn("已复制 文件 b 大小1.0KiB", out)

    def test_report_rich_path(self):
        con = MagicMock()
        ctx = _ctx(self.d, log="all", console=con)
        ctx.report("已同步", "软链接", os.path.join(self.d, "l"), extra="=> t")
        con.print.assert_called_once()
        rendered = str(con.print.call_args[0][0])
        self.assertIn("已同步", rendered)
        self.assertIn("=> t", rendered)

    def test_advance_updates_progress_and_plain(self):
        prog, pp = MagicMock(), MagicMock()
        ctx = _ctx(self.d, progress=prog, task_id=7, plain_progress=pp)
        ctx.stats.copied_files = 2
        ctx.stats.copied_bytes = 2048
        ctx.advance("a/b")
        self.assertEqual(ctx.stats.processed_entries, 1)
        kwargs = prog.update.call_args.kwargs
        self.assertEqual(kwargs["bytes"], "2.0KiB")
        pp.update_counts.assert_called_once()
        pp.advance.assert_called_once_with(path="a/b")


class TestShouldCopyFile(_TmpCase):
    def test_dst_is_directory_returns_true(self):
        src = self._file("s.txt")
        dst = os.path.join(self.d, "dstdir")
        os.makedirs(dst)
        self.assertTrue(should_copy_file(src, dst, checksum=True))

    def test_no_checksum_compares_mtime(self):
        src = self._file("s.txt", "same")
        dst = self._file("d.txt", "same")
        os.utime(dst, ns=(111_000_000_000, 111_000_000_000))
        self.assertTrue(should_copy_file(src, dst, checksum=False))
        shutil.copystat(src, dst)
        self.assertFalse(should_copy_file(src, dst, checksum=False))

    def test_checksum_same_size_diff_mtime_same_md5(self):
        src = self._file("s.txt", "abcd")
        dst = self._file("d.txt", "abcd")
        os.utime(dst, ns=(111_000_000_000, 111_000_000_000))
        self.assertFalse(should_copy_file(src, dst, checksum=True))

    def test_checksum_symlink_pair_compares_targets(self):
        t1, t2 = self._file("t1", "aa"), self._file("t2", "bb")
        src = os.path.join(self.d, "sl")
        dst = os.path.join(self.d, "dl")
        os.symlink(t1, src)
        os.symlink(t2, dst)
        os.utime(dst, ns=(111_000_000_000, 111_000_000_000), follow_symlinks=False)
        self.assertTrue(should_copy_file(src, dst, checksum=True))


class TestVerifyHelpers(_TmpCase):
    def test_verify_symlink_ok(self):
        target = self._file("t.txt")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(target, src)
        os.symlink(target, dst)
        self.assertEqual(verify_symlink_equal(src, dst), target)

    def test_verify_symlink_dst_not_link_exits(self):
        target = self._file("t.txt")
        src = os.path.join(self.d, "a")
        os.symlink(target, src)
        dst = self._file("plain.txt")
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as cm:
            verify_symlink_equal(src, dst)
        self.assertEqual(cm.exception.code, 1)

    def test_verify_symlink_target_mismatch_exits(self):
        t1, t2 = self._file("t1"), self._file("t2")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(t1, src)
        os.symlink(t2, dst)
        buf = io.StringIO()
        with patch("sys.stderr", buf), self.assertRaises(SystemExit):
            verify_symlink_equal(src, dst)
        self.assertIn("符号链接目标不一致", buf.getvalue())

    def test_verify_md5_equal(self):
        src = self._file("s.txt", "same")
        dst = self._file("d.txt", "same")
        a, b = verify_file_md5_equal(src, dst)
        self.assertEqual(a, b)

    def test_verify_md5_mismatch_exits(self):
        src = self._file("s.txt", "a")
        dst = self._file("d.txt", "b")
        buf = io.StringIO()
        with patch("sys.stderr", buf), self.assertRaises(SystemExit):
            verify_file_md5_equal(src, dst)
        self.assertIn("md5 校验失败", buf.getvalue())


class TestSymlinkHandling(_TmpCase):
    def test_should_copy_symlink_dst_missing(self):
        target = self._file("t.txt")
        src = os.path.join(self.d, "a")
        os.symlink(target, src)
        self.assertTrue(should_copy_symlink(src, os.path.join(self.d, "nope")))

    def test_should_copy_symlink_readlink_error(self):
        target = self._file("t.txt")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(target, src)
        os.symlink(target, dst)
        with patch("os.readlink", side_effect=[target, OSError]):
            self.assertTrue(should_copy_symlink(src, dst))

    def test_copy_symlink_replaces_existing(self):
        target = self._file("t.txt")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(target, src)
        with open(dst, "w") as f:
            f.write("old")
        core.copy_symlink(src, dst)
        self.assertTrue(os.path.islink(dst))

    def test_copy_file_syncs_symlink_and_verifies(self):
        target = self._file("t.txt")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(target, src)
        ctx = _ctx(self.d, verify_md5=True, log="all")
        with patch("sys.stderr", io.StringIO()):
            copy_file(src, dst, ctx)
        self.assertEqual(ctx.stats.synced_links, 1)
        self.assertEqual(ctx.stats.processed_links, 1)

    def test_copy_file_skips_identical_symlink(self):
        target = self._file("t.txt")
        src, dst = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        os.symlink(target, src)
        os.symlink(target, dst)
        ctx = _ctx(self.d)
        copy_file(src, dst, ctx)
        self.assertEqual(ctx.stats.skipped_links, 1)

    def test_copy_file_replaces_symlink_dst(self):
        src = self._file("s.txt", "content")
        target = self._file("t.txt")
        dst = os.path.join(self.d, "d")
        os.symlink(target, dst)
        ctx = _ctx(self.d)
        copy_file(src, dst, ctx)
        self.assertFalse(os.path.islink(dst))
        self.assertEqual(ctx.stats.copied_files, 1)

    def test_copy_file_skips_identical_file(self):
        src = self._file("s.txt", "same")
        dst = self._file("d.txt", "same")
        shutil.copystat(src, dst)
        ctx = _ctx(self.d)
        copy_file(src, dst, ctx)
        self.assertEqual(ctx.stats.skipped_files, 1)
        self.assertEqual(ctx.stats.copied_files, 0)

    def test_copy_file_replaces_dir_dst(self):
        src = self._file("s.txt", "content")
        dst = os.path.join(self.d, "d")
        os.makedirs(dst)
        ctx = _ctx(self.d)
        copy_file(src, dst, ctx)
        self.assertTrue(os.path.isfile(dst))


class TestEnsureDirForCopy(_TmpCase):
    def test_copystat_failure_falls_back_to_chmod(self):
        src_dir = os.path.join(self.d, "s")
        dst_dir = os.path.join(self.d, "d")
        os.makedirs(src_dir)
        ctx = _ctx(self.d)
        with patch("shutil.copystat", side_effect=OSError):
            self.assertTrue(ensure_dir_for_copy(dst_dir, src_dir=src_dir, ctx=ctx))
        self.assertTrue(os.path.isdir(dst_dir))
        self.assertEqual(ctx.stats.created_dirs, 1)

    def test_chmod_failure_is_swallowed(self):
        src_dir = os.path.join(self.d, "s")
        dst_dir = os.path.join(self.d, "d")
        os.makedirs(src_dir)
        ctx = _ctx(self.d)
        with patch("shutil.copystat", side_effect=OSError), \
             patch("os.chmod", side_effect=OSError):
            ensure_dir_for_copy(dst_dir, src_dir=src_dir, ctx=ctx)
        self.assertTrue(os.path.isdir(dst_dir))

    def test_existing_dir_counts_as_skipped(self):
        dst_dir = os.path.join(self.d, "d")
        os.makedirs(dst_dir)
        ctx = _ctx(self.d)
        self.assertFalse(ensure_dir_for_copy(dst_dir, src_dir=None, ctx=ctx))
        self.assertEqual(ctx.stats.skipped_dirs, 1)


class TestCopyTreeEdges(_TmpCase):
    def test_src_symlink_dir_handled_as_link(self):
        real = os.path.join(self.d, "real")
        os.makedirs(real)
        src = os.path.join(self.d, "link")
        os.symlink(real, src)
        ctx = _ctx(self.d)
        copy_tree(src, os.path.join(self.d, "out"), ctx)
        self.assertEqual(ctx.stats.processed_links, 1)
        self.assertTrue(os.path.islink(os.path.join(self.d, "out")))

    def test_dst_existing_file_replaced_by_dir(self):
        src = os.path.join(self.d, "s")
        os.makedirs(src)
        dst = self._file("d")
        ctx = _ctx(self.d)
        copy_tree(src, dst, ctx)
        self.assertTrue(os.path.isdir(dst))

    def test_symlinked_subdir_copied_as_link(self):
        src = os.path.join(self.d, "s")
        os.makedirs(os.path.join(src, "real"))
        os.symlink(os.path.join(src, "real"), os.path.join(src, "linkdir"))
        ctx = _ctx(self.d)
        copy_tree(src, os.path.join(self.d, "out"), ctx)
        self.assertTrue(os.path.islink(os.path.join(self.d, "out", "linkdir")))
        self.assertEqual(ctx.stats.processed_links, 1)


class TestDeleteExtraEntries(_TmpCase):
    def _pair(self):
        src, dst = os.path.join(self.d, "s"), os.path.join(self.d, "d")
        os.makedirs(src)
        os.makedirs(dst)
        return src, dst

    def test_deletes_extra_dir_and_symlink(self):
        src, dst = self._pair()
        os.makedirs(os.path.join(dst, "extradir"))
        os.symlink(self._file("t.txt"), os.path.join(dst, "extralink"))
        ctx = _ctx(self.d)
        delete_extra_entries(src_root=src, dst_root=dst, include_hidden=True, ctx=ctx)
        self.assertEqual(ctx.stats.deleted_dirs, 1)
        self.assertEqual(ctx.stats.deleted_links, 1)

    def test_hidden_entries_skipped_when_excluded(self):
        src, dst = self._pair()
        with open(os.path.join(dst, ".secret"), "w") as f:
            f.write("x")
        ctx = _ctx(self.d)
        delete_extra_entries(src_root=src, dst_root=dst, include_hidden=False, ctx=ctx)
        self.assertTrue(os.path.exists(os.path.join(dst, ".secret")))
        self.assertEqual(ctx.stats.deleted_files, 0)

    def test_recurses_into_common_dirs(self):
        src, dst = self._pair()
        os.makedirs(os.path.join(src, "sub"))
        os.makedirs(os.path.join(dst, "sub"))
        with open(os.path.join(dst, "sub", "gone.txt"), "w") as f:
            f.write("x")
        ctx = _ctx(self.d)
        delete_extra_entries(src_root=src, dst_root=dst, include_hidden=True, ctx=ctx)
        self.assertFalse(os.path.exists(os.path.join(dst, "sub", "gone.txt")))
        self.assertEqual(ctx.stats.deleted_files, 1)

    def test_symlink_dst_root_exits(self):
        src, dst = self._pair()
        link = os.path.join(self.d, "dlink")
        os.symlink(dst, link)
        buf = io.StringIO()
        with patch("sys.stderr", buf), self.assertRaises(SystemExit) as cm:
            delete_extra_entries(src_root=src, dst_root=link, include_hidden=True, ctx=_ctx(self.d))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("真实目录", buf.getvalue())

    def test_vanished_dst_root_returns_quietly(self):
        src, dst = self._pair()
        ctx = _ctx(self.d)
        with patch("os.scandir", side_effect=FileNotFoundError):
            delete_extra_entries(src_root=src, dst_root=dst, include_hidden=True, ctx=ctx)
        self.assertEqual(ctx.stats.processed_entries, 0)


class TestCountTreeOps(_TmpCase):
    def test_missing_path_counts_one(self):
        self.assertEqual(count_tree_ops(os.path.join(self.d, "nope")), 1)

    def test_plain_file_counts_one(self):
        self.assertEqual(count_tree_ops(self._file("f.txt")), 1)

    def test_symlinked_subdir_counted_once(self):
        src = os.path.join(self.d, "s")
        os.makedirs(os.path.join(src, "real"))
        os.symlink(os.path.join(src, "real"), os.path.join(src, "linkdir"))
        with open(os.path.join(src, "real", "f.txt"), "w") as f:
            f.write("x")
        # 根目录 1 + linkdir 1 + real 目录 1 + real/f.txt 1
        self.assertEqual(count_tree_ops(src), 4)


if __name__ == "__main__":
    unittest.main()
