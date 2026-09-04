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
        printed: list[object] = []

        class FakeConsole:
            def __init__(self, *a, **kw) -> None:
                pass

            def print(self, text="") -> None:
                printed.append(text)

        with mock.patch("rich.console.Console", FakeConsole):
            fb._render_fire_help(lines, io.StringIO())
        return printed

    def test_compact_help_shows_name_usage_and_choices(self) -> None:
        out = self._render([
            "NAME", "    cicd - CI 工具", "",
            "SYNOPSIS", "    cicd COMMAND | <flags>", "",
            "DESCRIPTION", "    常用：", "      cicd now", "",
            "COMMANDS", "    COMMAND is one of the following:", "", "     now", "       查看状态",
        ])
        joined = "\n".join(str(item) for item in out)
        self.assertIn("cicd", joined)
        self.assertIn("用法", joined)
        self.assertIn("cicd now", joined)
        self.assertIn("查看状态", joined)

    def test_empty_input_prints_minimal_head(self) -> None:
        self.assertEqual([str(item) for item in self._render([])], ["help"])


class TestHelpParsers(unittest.TestCase):
    def test_help_choices_extracts_command_descriptions(self) -> None:
        text = "COMMANDS\n    COMMAND is one of the following:\n\n     now\n       查看状态\n\n     run\n       触发 CI"
        self.assertEqual(fb._help_choices(text, "COMMANDS"), [("now", "查看状态"), ("run", "触发 CI")])


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

    def test_skills_exits_before_fire(self) -> None:
        code, fire_fn = self._run(0, argv=["prog", "--skills"])
        self.assertEqual(code, 0)
        fire_fn.assert_not_called()

    def test_builtins_print_is_restored(self) -> None:
        import builtins
        before = builtins.print
        self._run(0)
        self.assertIs(builtins.print, before)


if __name__ == "__main__":
    unittest.main()
