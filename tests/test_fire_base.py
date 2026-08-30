"""lib/fire_base.py：耗时装饰器、fire 入口包装、help 渲染。

run_cli 会调 sys.exit，测试里一律用 assertRaises(SystemExit) 接住；fire.Fire
本身被替换成假的，不解析真实 argv。
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import fire_base as fb  # noqa: E402


class TestBaseCli(unittest.TestCase):
    def test_reporter_is_stored_under_an_underscore_name(self) -> None:
        cli = fb.BaseCli()
        self.assertTrue(hasattr(cli, "_r"))
        # fire 把公开属性列成 group，所以不能有裸 `r`
        self.assertFalse(hasattr(cli, "r"))


class TestTimedCli(unittest.TestCase):
    """耗时行走 rich Console(stderr)，这里拦 Console 拿到渲染出的文本。"""

    def _run(self, elapsed: float, fn=None):
        printed: list[str] = []

        class FakeConsole:
            def __init__(self, *a, **kw) -> None:
                pass

            def print(self, text) -> None:
                printed.append(str(text))

        clock = iter([0.0, elapsed])

        class Cli(fb.BaseCli):
            @fb.timed_cli
            def go(self):
                if fn:
                    return fn()
                return 7

        with mock.patch("rich.console.Console", FakeConsole), \
             mock.patch.object(fb.time, "monotonic", lambda: next(clock)):
            try:
                rc = Cli().go()
            except RuntimeError:
                rc = None
        return rc, printed[0] if printed else ""

    def test_return_value_passes_through(self) -> None:
        rc, _ = self._run(0.1)
        self.assertEqual(rc, 7)

    def test_sub_second_uses_milliseconds(self) -> None:
        _, line = self._run(0.25)
        self.assertIn("250ms", line)

    def test_seconds(self) -> None:
        _, line = self._run(3.5)
        self.assertIn("3.5s", line)

    def test_minutes(self) -> None:
        _, line = self._run(95.0)
        self.assertIn("1m35s", line)

    def test_timing_still_prints_when_the_method_raises(self) -> None:
        def boom():
            raise RuntimeError("炸了")

        _, line = self._run(1.0, fn=boom)
        self.assertIn("⏱", line)


class TestHandleFireResult(unittest.TestCase):
    def test_swallows_the_value(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            self.assertIsNone(fb._handle_fire_result(["trace"]))
        self.assertEqual(buf.getvalue(), "")


class TestRenderFireInfo(unittest.TestCase):
    def test_prints_the_info_line_dimmed(self) -> None:
        printed: list[str] = []

        class FakeConsole:
            def __init__(self, *a, **kw) -> None:
                pass

            def print(self, text) -> None:
                printed.append(text)

        with mock.patch("rich.console.Console", FakeConsole):
            fb._render_fire_info(("INFO: Showing help\n",), {})
        self.assertEqual(printed, ["[dim]INFO: Showing help[/dim]"])


class TestRenderFireHelp(unittest.TestCase):
    def _render(self, lines: list[str]) -> list[str]:
        printed: list[str] = []

        class FakeConsole:
            def __init__(self, *a, **kw) -> None:
                pass

            def print(self, text="") -> None:
                printed.append(text)

        with mock.patch("rich.console.Console", FakeConsole):
            fb._render_fire_help(lines, io.StringIO())
        return printed

    def test_section_titles_are_highlighted(self) -> None:
        out = self._render(["NAME", "    ovpn - VPN 客户端"])
        self.assertIn("[bold cyan]NAME[/bold cyan]", out)
        self.assertIn("      ovpn - VPN 客户端", out)

    def test_multi_word_titles_are_kept_as_titles(self) -> None:
        out = self._render(["POSITIONAL ARGUMENTS", "    HOST"])
        # 含空格 → 不当标题，按续段原样缩进输出
        self.assertNotIn("[bold cyan]POSITIONAL ARGUMENTS[/bold cyan]", out)
        self.assertIn("  POSITIONAL ARGUMENTS", out)

    def test_continuation_chunks_are_indented(self) -> None:
        out = self._render(["    X is one of the following:", "        a"])
        self.assertIn("      X is one of the following:", out)

    def test_blank_lines_inside_a_section_survive(self) -> None:
        out = self._render(["NAME", "    a", "", "    b"])
        self.assertIn("", out)

    def test_empty_input_prints_nothing(self) -> None:
        self.assertEqual(self._render([]), [])


class TestRunCli(unittest.TestCase):
    def _run(self, fire_result, argv=None):
        cli = fb.BaseCli()
        fake_cio = mock.MagicMock()
        fake_core = mock.MagicMock()
        modules = {"fire.console.console_io": fake_cio, "fire.core": fake_core}
        real_import = __import__

        def fake_import(name, *a, **kw):
            if name in modules:
                return modules[name]
            return real_import(name, *a, **kw)

        # run_cli 经 consume_* 改 os.environ 和 lib.notify 的模块级开关，
        # 两者都得还原，否则污染同进程里后面的 test_notify
        import lib.notify as notify
        say, dbg = notify.is_say_disabled(), notify.is_debug()
        self.addCleanup(notify.set_say_disabled, say)
        self.addCleanup(notify.set_debug, dbg)

        with mock.patch.dict(os.environ), \
             mock.patch.object(sys, "argv", argv or ["prog", "status"]), \
             mock.patch.object(fb.fire, "Fire", return_value=fire_result) as fire_fn, \
             mock.patch.dict(sys.modules, modules), \
             mock.patch("builtins.__import__", fake_import):
            with self.assertRaises(SystemExit) as ctx:
                fb.run_cli(cli)
        return ctx.exception.code, fire_fn

    def test_int_return_becomes_the_exit_code(self) -> None:
        code, _ = self._run(3)
        self.assertEqual(code, 3)

    def test_none_return_exits_zero(self) -> None:
        code, _ = self._run(None)
        self.assertEqual(code, 0)

    def test_global_flags_are_stripped_before_fire_sees_them(self) -> None:
        self._run(0, argv=["prog", "status", "--debug", "--no-say"])
        self.assertNotIn("--debug", sys.argv)
        self.assertNotIn("--no-say", sys.argv)

    def test_builtins_print_is_restored(self) -> None:
        import builtins
        before = builtins.print
        self._run(0)
        self.assertIs(builtins.print, before)


if __name__ == "__main__":
    unittest.main()
