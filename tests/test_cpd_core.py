#!/usr/bin/env python3
"""cpd_core 模块的单元测试"""
import hashlib
import os
import tempfile
import unittest

from lib.cpd_core import (
    RunCtx,
    Stats,
    copy_file,
    copy_symlink,
    copy_tree,
    count_tree_ops,
    delete_extra_entries,
    ensure_dir,
    fmt_size,
    md5_file,
    remove_any,
    should_copy_file,
    should_copy_symlink,
)


class TestCpdCore(unittest.TestCase):
    """cpd_core 核心函数测试"""

    def setUp(self):
        """创建临时测试目录"""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """清理临时测试目录"""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_fmt_size_formats_bytes_correctly(self):
        """测试字节大小格式化"""
        self.assertEqual(fmt_size(0), "0B")
        self.assertEqual(fmt_size(512), "512B")
        self.assertEqual(fmt_size(1024), "1.0KiB")
        self.assertEqual(fmt_size(1536), "1.5KiB")
        self.assertEqual(fmt_size(1024 * 1024), "1.0MiB")
        self.assertEqual(fmt_size(1024 * 1024 * 1024), "1.0GiB")
        self.assertEqual(fmt_size(None), "0B")
        self.assertEqual(fmt_size(-100), "0B")

    def test_md5_file_computes_correct_hash(self):
        """测试 MD5 哈希计算"""
        test_file = os.path.join(self.test_dir, "test.txt")
        content = b"Hello, World!"
        with open(test_file, "wb") as f:
            f.write(content)

        expected_md5 = hashlib.md5(content).hexdigest()
        actual_md5 = md5_file(test_file)
        self.assertEqual(actual_md5, expected_md5)

    def test_should_copy_file_returns_true_for_missing_dest(self):
        """测试目标文件不存在时返回 True"""
        src = os.path.join(self.test_dir, "src.txt")
        dst = os.path.join(self.test_dir, "dst.txt")
        with open(src, "w") as f:
            f.write("test")

        self.assertTrue(should_copy_file(src, dst, checksum=True))

    def test_should_copy_file_returns_false_for_identical_files(self):
        """测试相同文件（MD5、大小、时间戳）返回 False"""
        src = os.path.join(self.test_dir, "src.txt")
        dst = os.path.join(self.test_dir, "dst.txt")
        content = "identical content"
        with open(src, "w") as f:
            f.write(content)
        with open(dst, "w") as f:
            f.write(content)

        # 复制时间戳
        import shutil

        shutil.copystat(src, dst)

        self.assertFalse(should_copy_file(src, dst, checksum=True))

    def test_should_copy_file_returns_true_for_different_content(self):
        """测试不同内容的文件返回 True"""
        src = os.path.join(self.test_dir, "src.txt")
        dst = os.path.join(self.test_dir, "dst.txt")
        with open(src, "w") as f:
            f.write("source content")
        with open(dst, "w") as f:
            f.write("dest content")

        self.assertTrue(should_copy_file(src, dst, checksum=True))

    def test_remove_any_removes_file(self):
        """测试删除文件"""
        test_file = os.path.join(self.test_dir, "file.txt")
        with open(test_file, "w") as f:
            f.write("test")

        self.assertTrue(os.path.exists(test_file))
        remove_any(test_file)
        self.assertFalse(os.path.exists(test_file))

    def test_remove_any_removes_directory(self):
        """测试删除目录"""
        test_dir = os.path.join(self.test_dir, "subdir")
        os.makedirs(test_dir)
        test_file = os.path.join(test_dir, "file.txt")
        with open(test_file, "w") as f:
            f.write("test")

        self.assertTrue(os.path.exists(test_dir))
        remove_any(test_dir)
        self.assertFalse(os.path.exists(test_dir))

    def test_remove_any_removes_symlink(self):
        """测试删除符号链接"""
        target = os.path.join(self.test_dir, "target.txt")
        link = os.path.join(self.test_dir, "link.txt")
        with open(target, "w") as f:
            f.write("test")
        os.symlink(target, link)

        self.assertTrue(os.path.islink(link))
        remove_any(link)
        self.assertFalse(os.path.exists(link))
        self.assertTrue(os.path.exists(target))  # 目标文件应保留

    def test_ensure_dir_creates_directory(self):
        """测试创建目录"""
        test_dir = os.path.join(self.test_dir, "new", "nested", "dir")
        self.assertFalse(os.path.exists(test_dir))
        ensure_dir(test_dir)
        self.assertTrue(os.path.isdir(test_dir))

    def test_ensure_dir_is_idempotent(self):
        """测试重复创建目录不报错"""
        test_dir = os.path.join(self.test_dir, "existing")
        os.makedirs(test_dir)
        self.assertTrue(os.path.isdir(test_dir))
        ensure_dir(test_dir)  # 不应抛出异常
        self.assertTrue(os.path.isdir(test_dir))

    def test_copy_symlink_creates_correct_link(self):
        """测试复制符号链接"""
        target = os.path.join(self.test_dir, "target.txt")
        src_link = os.path.join(self.test_dir, "src_link.txt")
        dst_link = os.path.join(self.test_dir, "dst_link.txt")

        with open(target, "w") as f:
            f.write("target content")
        os.symlink(target, src_link)

        copy_symlink(src_link, dst_link)
        self.assertTrue(os.path.islink(dst_link))
        self.assertEqual(os.readlink(src_link), os.readlink(dst_link))

    def test_should_copy_symlink_returns_true_for_different_target(self):
        """测试符号链接目标不同时返回 True"""
        target1 = os.path.join(self.test_dir, "target1.txt")
        target2 = os.path.join(self.test_dir, "target2.txt")
        src_link = os.path.join(self.test_dir, "src_link.txt")
        dst_link = os.path.join(self.test_dir, "dst_link.txt")

        with open(target1, "w") as f:
            f.write("target1")
        with open(target2, "w") as f:
            f.write("target2")

        os.symlink(target1, src_link)
        os.symlink(target2, dst_link)

        self.assertTrue(should_copy_symlink(src_link, dst_link))

    def test_should_copy_symlink_returns_false_for_same_target(self):
        """测试符号链接目标相同时返回 False"""
        target = os.path.join(self.test_dir, "target.txt")
        src_link = os.path.join(self.test_dir, "src_link.txt")
        dst_link = os.path.join(self.test_dir, "dst_link.txt")

        with open(target, "w") as f:
            f.write("target")

        os.symlink(target, src_link)
        os.symlink(target, dst_link)

        self.assertFalse(should_copy_symlink(src_link, dst_link))

    def test_copy_file_copies_regular_file(self):
        """测试复制普通文件"""
        src = os.path.join(self.test_dir, "src.txt")
        dst = os.path.join(self.test_dir, "dst.txt")
        content = "test content"
        with open(src, "w") as f:
            f.write(content)

        ctx = RunCtx(
            checksum=True,
            verify_md5=False,
            log="quiet",
            display_base=self.test_dir,
            stats=Stats(),
            console=None,
            progress=None,
            task_id=None,
            plain_progress=None,
        )

        copy_file(src, dst, ctx)
        self.assertTrue(os.path.exists(dst))
        with open(dst) as f:
            self.assertEqual(f.read(), content)
        self.assertEqual(ctx.stats.copied_files, 1)

    def test_copy_tree_copies_directory_recursively(self):
        """测试递归复制目录"""
        src_dir = os.path.join(self.test_dir, "src")
        dst_dir = os.path.join(self.test_dir, "dst")

        # 创建源目录结构
        os.makedirs(os.path.join(src_dir, "subdir"))
        with open(os.path.join(src_dir, "file1.txt"), "w") as f:
            f.write("file1")
        with open(os.path.join(src_dir, "subdir", "file2.txt"), "w") as f:
            f.write("file2")

        ctx = RunCtx(
            checksum=True,
            verify_md5=False,
            log="quiet",
            display_base=self.test_dir,
            stats=Stats(),
            console=None,
            progress=None,
            task_id=None,
            plain_progress=None,
        )

        copy_tree(src_dir, dst_dir, ctx)

        # 验证目标目录结构
        self.assertTrue(os.path.isdir(dst_dir))
        self.assertTrue(os.path.isfile(os.path.join(dst_dir, "file1.txt")))
        self.assertTrue(os.path.isdir(os.path.join(dst_dir, "subdir")))
        self.assertTrue(os.path.isfile(os.path.join(dst_dir, "subdir", "file2.txt")))

        with open(os.path.join(dst_dir, "file1.txt")) as f:
            self.assertEqual(f.read(), "file1")
        with open(os.path.join(dst_dir, "subdir", "file2.txt")) as f:
            self.assertEqual(f.read(), "file2")

        # 验证统计信息
        self.assertEqual(ctx.stats.copied_files, 2)
        self.assertEqual(ctx.stats.created_dirs, 2)

    def test_delete_extra_entries_removes_only_extra_files(self):
        """测试删除多余文件（-f 模式）"""
        src_dir = os.path.join(self.test_dir, "src")
        dst_dir = os.path.join(self.test_dir, "dst")

        # 创建源目录
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "keep.txt"), "w") as f:
            f.write("keep")

        # 创建目标目录（包含多余文件）
        os.makedirs(dst_dir)
        with open(os.path.join(dst_dir, "keep.txt"), "w") as f:
            f.write("keep")
        with open(os.path.join(dst_dir, "extra.txt"), "w") as f:
            f.write("extra")

        ctx = RunCtx(
            checksum=True,
            verify_md5=False,
            log="quiet",
            display_base=self.test_dir,
            stats=Stats(),
            console=None,
            progress=None,
            task_id=None,
            plain_progress=None,
        )

        delete_extra_entries(src_root=src_dir, dst_root=dst_dir, include_hidden=True, ctx=ctx)

        # 验证保留的文件存在，多余文件被删除
        self.assertTrue(os.path.exists(os.path.join(dst_dir, "keep.txt")))
        self.assertFalse(os.path.exists(os.path.join(dst_dir, "extra.txt")))
        self.assertEqual(ctx.stats.deleted_files, 1)

    def test_count_tree_ops_counts_correctly(self):
        """测试统计目录树操作数"""
        src_dir = os.path.join(self.test_dir, "src")
        os.makedirs(os.path.join(src_dir, "subdir"))
        with open(os.path.join(src_dir, "file1.txt"), "w") as f:
            f.write("file1")
        with open(os.path.join(src_dir, "file2.txt"), "w") as f:
            f.write("file2")
        with open(os.path.join(src_dir, "subdir", "file3.txt"), "w") as f:
            f.write("file3")

        # 预期：1 个根目录 + 1 个子目录 + 3 个文件 = 5 个操作
        ops = count_tree_ops(src_dir)
        self.assertEqual(ops, 5)

    def test_count_tree_ops_handles_symlink(self):
        """测试统计符号链接的操作数"""
        target = os.path.join(self.test_dir, "target.txt")
        link = os.path.join(self.test_dir, "link.txt")
        with open(target, "w") as f:
            f.write("target")
        os.symlink(target, link)

        ops = count_tree_ops(link)
        self.assertEqual(ops, 1)

    def test_stats_initialization(self):
        """测试统计对象初始化"""
        stats = Stats()
        self.assertEqual(stats.copied_files, 0)
        self.assertEqual(stats.skipped_files, 0)
        self.assertEqual(stats.created_dirs, 0)
        self.assertEqual(stats.copied_bytes, 0)

    def test_run_ctx_dst_rel_computes_relative_path(self):
        """测试 RunCtx._dst_rel 计算相对路径"""
        ctx = RunCtx(
            checksum=True,
            verify_md5=False,
            log="all",
            display_base="/base/dir",
            stats=Stats(),
            console=None,
            progress=None,
            task_id=None,
            plain_progress=None,
        )

        rel = ctx._dst_rel("/base/dir/subdir/file.txt")
        self.assertEqual(rel, "subdir/file.txt")

        rel = ctx._dst_rel("/base/dir")
        self.assertEqual(rel, ".")


if __name__ == "__main__":
    unittest.main()
