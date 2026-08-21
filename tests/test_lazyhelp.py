"""lazyhelp 单元测试。"""

from __future__ import annotations

import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.lazyhelp import TOOLS, CATEGORIES_ORDER, _all_bins, _render_table, main, show_full
from lib.ui import Reporter


class TestRegistry(unittest.TestCase):
    def test_all_bin_entries_registered(self) -> None:
        """每个 bin/ 入口（薄壳 + 内部 _gitwf）都应在 TOOLS 中声明（除 lazyhelp 自己）。"""
        bin_dir = pathlib.Path(__file__).resolve().parent.parent / "bin"
        actual = {p.name for p in bin_dir.iterdir()
                  if not p.name.startswith(".") and p.is_file() or p.is_symlink()}
        actual.discard("lazyhelp")  # 自指，排除
        actual.discard("_gitwf")  # 内部 symlink 入口（merge_*/push_* 复用），不直接展示
        registered = set(TOOLS)
        missing = actual - registered
        extra = registered - actual
        self.assertFalse(missing, f"bin/ 中存在但未注册的工具: {sorted(missing)}")
        self.assertFalse(extra, f"TOOLS 中存在但 bin/ 不存在的工具: {sorted(extra)}")

    def test_categories_valid(self) -> None:
        for name, (cat, _desc) in TOOLS.items():
            self.assertIn(cat, CATEGORIES_ORDER, f"{name} 分类 {cat!r} 不在已知分类列表")

    def test_descriptions_non_empty(self) -> None:
        for name, (_cat, desc) in TOOLS.items():
            self.assertTrue(desc.strip(), f"{name} 描述为空")


class TestAllBins(unittest.TestCase):
    def test_includes_known_bins(self) -> None:
        names = _all_bins()
        self.assertIn("lazyhelp", names)
        self.assertIn("cpd", names)
        self.assertIn("merge_master", names)
        self.assertIn("_gitwf", names)

    def test_sorted(self) -> None:
        names = _all_bins()
        self.assertEqual(names, sorted(names))


class TestRenderTable(unittest.TestCase):
    def test_render_table_empty(self) -> None:
        buf = io.StringIO()
        r = Reporter(file=buf)
        _render_table([], r)
        self.assertIn("无匹配工具", buf.getvalue())

    def test_render_table_groups_by_category(self) -> None:
        rows = [(name, cat, desc) for name, (cat, desc) in sorted(TOOLS.items())
                if cat == "git-wf"]
        buf = io.StringIO()
        r = Reporter(file=buf)
        _render_table(rows, r)
        out = buf.getvalue()
        self.assertIn("git-wf", out)
        self.assertIn("merge_canary", out)


class TestShowFull(unittest.TestCase):
    def test_unknown_bin(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = show_full("definitely_not_a_real_bin_xyz")
        self.assertEqual(rc, 2)
        self.assertIn("未在 bin/ 中找到", err.getvalue())

    def test_known_bin_invokes_help(self) -> None:
        # 真实调 bin/cpd --help，应成功退出
        rc = show_full("cpd")
        self.assertEqual(rc, 0)


class TestMainDefault(unittest.TestCase):
    def test_no_args_renders_overview(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["lazyhelp"])
        self.assertEqual(rc, 0)
        out = err.getvalue()
        self.assertIn("工具速查", out)
        self.assertIn("git-wf", out)
        # 用法提示
        self.assertIn("lazyhelp <工具名>", out)
        # 全部工具名都应在概览中出现
        for name in TOOLS:
            self.assertIn(name, out, f"默认概览缺少 {name}")


class TestMainDispatch(unittest.TestCase):
    def test_known_tool_runs_help(self) -> None:
        # 实跑：cpd 的 --help 退出码 0
        rc = main(["lazyhelp", "cpd"])
        self.assertEqual(rc, 0)

    def test_internal_symlink_runs_help(self) -> None:
        # _gitwf 在 bin/ 但不在 TOOLS — 仍应透传
        rc = main(["lazyhelp", "_gitwf"])
        self.assertEqual(rc, 0)

    def test_unknown_tool_falls_back_to_overview(self) -> None:
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["lazyhelp", "definitely_not_a_real_bin_xyz"])
        self.assertEqual(rc, 0)  # 仍成功，仅提示
        out = err.getvalue()
        self.assertIn("未找到", out)
        # 概览仍渲染
        self.assertIn("工具速查", out)


if __name__ == "__main__":
    unittest.main()
