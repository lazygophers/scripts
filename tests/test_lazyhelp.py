"""lazyhelp 单元测试。"""

from __future__ import annotations

import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.lazyhelp import TOOLS, CATEGORIES_ORDER, _render_table, list_tools, main, show_full
from lib.ui import Reporter


class TestRegistry(unittest.TestCase):
    def test_all_bin_entries_registered(self) -> None:
        """每个 bin/ 入口（薄壳 + 内部 _gitwf）都应在 TOOLS 中声明（除 lazyhelp 自己）。"""
        bin_dir = pathlib.Path(__file__).resolve().parent.parent / "bin"
        actual = {p.name for p in bin_dir.iterdir() if not p.name.startswith(".")}
        actual.discard("lazyhelp")  # 自指，排除
        # _gitwf 是内部 symlink 入口（merge_*/push_* 复用），不直接展示
        actual.discard("_gitwf")
        registered = set(TOOLS)
        missing = actual - registered
        extra = registered - actual
        self.assertFalse(missing, f"bin/ 中存在但未注册的工具: {sorted(missing)}")
        self.assertFalse(extra, f"TOOLS 中存在但 bin/ 不存在的工具: {sorted(extra)}")

    def test_categories_valid(self) -> None:
        """每个工具的分类必须在 CATEGORIES_ORDER 中。"""
        for name, (cat, _desc) in TOOLS.items():
            self.assertIn(cat, CATEGORIES_ORDER, f"{name} 分类 {cat!r} 不在已知分类列表")

    def test_descriptions_non_empty(self) -> None:
        for name, (_cat, desc) in TOOLS.items():
            self.assertTrue(desc.strip(), f"{name} 描述为空")


class TestListTools(unittest.TestCase):
    def test_all_returns_all_sorted(self) -> None:
        rows = list_tools()
        self.assertEqual(len(rows), len(TOOLS))
        names = [r[0] for r in rows]
        self.assertEqual(names, sorted(names))

    def test_category_filter(self) -> None:
        rows = list_tools(category="git-wf")
        self.assertGreater(len(rows), 0)
        for _, cat, _ in rows:
            self.assertEqual(cat, "git-wf")

    def test_category_filter_empty(self) -> None:
        rows = list_tools(category="nonexistent")
        self.assertEqual(rows, [])


class TestRenderTable(unittest.TestCase):
    def test_render_table_empty(self) -> None:
        buf = io.StringIO()
        r = Reporter(file=buf)
        _render_table([], r)
        out = buf.getvalue()
        self.assertIn("无匹配工具", out)

    def test_render_table_groups_by_category(self) -> None:
        rows = list_tools(category="git-wf")
        buf = io.StringIO()
        r = Reporter(file=buf)
        _render_table(rows, r)
        out = buf.getvalue()
        self.assertIn("git-wf", out)
        # 第一条工具名应出现
        self.assertIn("merge_canary", out)


class TestMainFlags(unittest.TestCase):
    def test_list_outputs_names(self) -> None:
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["lazyhelp", "--list"])
        self.assertEqual(rc, 0)
        names = buf.getvalue().splitlines()
        self.assertEqual(len(names), len(TOOLS))
        self.assertIn("cpd", names)
        self.assertIn("merge_master", names)

    def test_search_filters(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["lazyhelp", "--search", "复制"])
        self.assertEqual(rc, 0)
        self.assertIn("cpd", err.getvalue())

    def test_search_no_match(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["lazyhelp", "--search", "不存在的关键字xyz"])
        self.assertEqual(rc, 0)
        self.assertIn("无匹配工具", err.getvalue())

    def test_full_unknown(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["lazyhelp", "--full", "no_such_tool"])
        self.assertEqual(rc, 2)
        self.assertIn("未知工具", err.getvalue())

    def test_full_calls_bin_help(self) -> None:
        # 真实调 bin/cpd --help，应成功退出
        rc = main(["lazyhelp", "--full", "cpd"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
