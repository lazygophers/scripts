#!/usr/bin/env python3
"""Tests for lib.project."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.project import safe_project_context


class TestSafeProjectContext(unittest.TestCase):
    def test_normal_path(self):
        with patch("os.getcwd", return_value="/home/u/myorg/myproject"):
            ctx = safe_project_context()
        self.assertIn("myproject", ctx)
        self.assertIn("myorg", ctx)

    def test_language_dir_parent_skipped(self):
        # 父目录是 "go"（语言目录）→ 再上溯
        with patch("os.getcwd", return_value="/home/u/go/myproject"):
            ctx = safe_project_context()
        self.assertIn("myproject", ctx)
        # org 应该是 "u" 而非 "go"
        self.assertIn("u", ctx)
        self.assertNotIn("go", ctx.split(" 的 ")[0])

    def test_root_path(self):
        # basename("/") 的父也是 "/"，org/project 都为空串
        with patch("os.getcwd", return_value="/"):
            ctx = safe_project_context()
        self.assertIsInstance(ctx, str)

    def test_language_dir_case_insensitive(self):
        with patch("os.getcwd", return_value="/home/u/Python/myproject"):
            ctx = safe_project_context()
        self.assertNotIn("Python", ctx.split(" 的 ")[0])


if __name__ == "__main__":
    unittest.main()
