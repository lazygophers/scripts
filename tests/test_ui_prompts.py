#!/usr/bin/env python3
"""lib/ui.py 的交互入口：单选菜单和三个输入框。

这几段是 coverage 里最后一块空白（443-476 那个按键循环整段没跑过）。
`ask_select` 特地留了 `read_key` 注入口，所以不需要真 TTY 也能测。
"""
from __future__ import annotations

import io
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from rich.console import Console

from lib import ui


def keys(*sequence: str):
    """把一串按键做成 read_key：按顺序返回，用完再调就是断言失败。"""
    pending = list(sequence)

    def read_key() -> str:
        if not pending:
            raise AssertionError("按键用完了还在读，说明循环没按预期退出")
        return pending.pop(0)

    return read_key


def select(*sequence: str, options=("甲", "乙", "丙"), current=None):
    buf = io.StringIO()
    chosen = ui.ask_select(
        "选一个", list(options), current=current,
        console=Console(file=buf, force_terminal=True), read_key=keys(*sequence),
    )
    return chosen, buf.getvalue()


class TestAskSelect(unittest.TestCase):
    def test_enter_takes_the_first_option_by_default(self):
        self.assertEqual(select("enter")[0], "甲")

    def test_down_moves_the_highlight(self):
        self.assertEqual(select("down", "enter")[0], "乙")

    def test_up_from_the_top_wraps_to_the_bottom(self):
        self.assertEqual(select("up", "enter")[0], "丙")

    def test_down_from_the_bottom_wraps_to_the_top(self):
        self.assertEqual(select("down", "down", "down", "enter")[0], "甲")

    def test_a_digit_selects_and_confirms_in_one_key(self):
        self.assertEqual(select("2")[0], "乙")

    def test_a_digit_out_of_range_is_ignored(self):
        # 9 超出范围，必须被忽略而不是选中或崩掉；随后的回车才定下来
        self.assertEqual(select("9", "enter")[0], "甲")

    def test_esc_cancels_and_returns_nothing(self):
        self.assertIsNone(select("esc")[0])

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(select("x", "\t", "enter")[0], "甲")

    def test_the_current_value_is_where_the_highlight_starts(self):
        self.assertEqual(select("enter", current="丙")[0], "丙")

    def test_an_unknown_current_value_falls_back_to_the_first(self):
        self.assertEqual(select("enter", current="不在表里")[0], "甲")

    def test_the_current_value_is_marked_in_the_menu(self):
        _, rendered = select("enter", current="乙")
        self.assertIn("←当前", rendered)
        self.assertIn("2. 乙", rendered)

    def test_every_option_is_numbered_in_order(self):
        _, rendered = select("enter")
        for i, name in enumerate(("甲", "乙", "丙"), start=1):
            self.assertIn(f"{i}. {name}", rendered)

    def test_without_a_tty_and_without_an_injected_reader_it_gives_up(self):
        """非交互环境返回 None，让调用方自己回退到别的输入方式。"""
        with patch.object(sys, "stdin", io.StringIO()):
            self.assertIsNone(ui.ask_select("选一个", ["甲"], console=Console(file=io.StringIO())))


class TestAskPrompts(unittest.TestCase):
    """三个输入框都要在非交互（EOF）时返回 None，而不是抛出去。"""

    def test_confirm_returns_the_answer(self):
        with patch("rich.prompt.Confirm.ask", return_value=True):
            self.assertIs(ui.ask_confirm("要继续吗"), True)

    def test_confirm_returns_none_on_eof(self):
        with patch("rich.prompt.Confirm.ask", side_effect=EOFError):
            self.assertIsNone(ui.ask_confirm("要继续吗"))

    def test_confirm_returns_none_on_interrupt(self):
        with patch("rich.prompt.Confirm.ask", side_effect=KeyboardInterrupt):
            self.assertIsNone(ui.ask_confirm("要继续吗"))

    def test_text_returns_the_answer_and_passes_the_default(self):
        with patch("rich.prompt.Prompt.ask", return_value="值") as ask:
            self.assertEqual(ui.ask_text("输入", default="缺省"), "值")
        self.assertEqual(ask.call_args.kwargs["default"], "缺省")

    def test_text_returns_none_on_eof(self):
        with patch("rich.prompt.Prompt.ask", side_effect=EOFError):
            self.assertIsNone(ui.ask_text("输入"))

    def test_secret_does_not_echo(self):
        with patch("rich.prompt.Prompt.ask", return_value="pw") as ask:
            self.assertEqual(ui.ask_secret("口令"), "pw")
        self.assertIs(ask.call_args.kwargs["password"], True)

    def test_secret_returns_none_on_interrupt(self):
        with patch("rich.prompt.Prompt.ask", side_effect=KeyboardInterrupt):
            self.assertIsNone(ui.ask_secret("口令"))


if __name__ == "__main__":
    unittest.main()
