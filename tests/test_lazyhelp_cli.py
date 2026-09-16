"""lazyhelp 的 fire CLI 层（`LazyhelpCli`）测试：目前只有 `install` 子命令。

`tests/test_lazyhelp.py` 覆盖的是 `lib/lazyhelp.py` 那层域函数（TOOLS 注册表、
`_render_table`、`main()` 的分发）；这里测的是 `lib/cli/lazyhelp.py` 里薄的
fire 包装——`install` 转调 `browse install` + `graphwatch install` 并汇总
退出码，外加逐个确认 / `-y` 跳过确认的分支。两边各自的安装逻辑、确认弹窗都是
假的，不真的跑 native messaging / launchd / 读终端输入。
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli.lazyhelp import LazyhelpCli


class TestInstall(unittest.TestCase):
    """`-y` 跳过确认，走真正的安装转调 + 退出码汇总。"""

    def setUp(self) -> None:
        self.cli = LazyhelpCli()
        self.cli._r = mock.MagicMock()

    def _run(self, browse_rc: int, graphwatch_rc: int) -> int:
        with mock.patch("lib.browse_install.main", return_value=browse_rc) as browse_main, \
             mock.patch("lib.graphwatch.GraphwatchCli.install", return_value=graphwatch_rc) as gw_install, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = self.cli.install(yes=True)
        self.browse_main = browse_main
        self.gw_install = gw_install
        return rc

    def test_both_succeed(self) -> None:
        rc = self._run(browse_rc=0, graphwatch_rc=0)
        self.assertEqual(rc, 0)
        self.browse_main.assert_called_once_with(["browse install"])
        self.gw_install.assert_called_once()

    def test_browse_fails_graphwatch_still_runs(self) -> None:
        rc = self._run(browse_rc=1, graphwatch_rc=0)
        self.assertEqual(rc, 1)
        self.gw_install.assert_called_once()  # 没被 browse 的失败拦住

    def test_graphwatch_fails_alone_is_still_nonzero(self) -> None:
        rc = self._run(browse_rc=0, graphwatch_rc=1)
        self.assertEqual(rc, 1)

    def test_both_fail(self) -> None:
        rc = self._run(browse_rc=1, graphwatch_rc=1)
        self.assertEqual(rc, 1)


class TestConfirmGate(unittest.TestCase):
    """不带 `-y` 时逐个问；答否/非交互(None) 都是跳过而不是失败。"""

    def setUp(self) -> None:
        self.cli = LazyhelpCli()
        self.cli._r = mock.MagicMock()

    def _run(self, confirm_answers):
        with mock.patch("lib.ui.ask_confirm", side_effect=confirm_answers) as ask, \
             mock.patch("lib.browse_install.main", return_value=0) as browse_main, \
             mock.patch("lib.graphwatch.GraphwatchCli.install", return_value=0) as gw_install, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = self.cli.install()
        self.ask = ask
        self.browse_main = browse_main
        self.gw_install = gw_install
        return rc

    def test_answer_yes_to_both_installs_both(self) -> None:
        rc = self._run([True, True])
        self.assertEqual(rc, 0)
        self.browse_main.assert_called_once()
        self.gw_install.assert_called_once()
        self.assertEqual(self.ask.call_count, 2)

    def test_answer_no_skips_without_failing(self) -> None:
        rc = self._run([False, False])
        self.assertEqual(rc, 0)  # 跳过不算失败
        self.browse_main.assert_not_called()
        self.gw_install.assert_not_called()

    def test_non_interactive_none_is_treated_as_decline(self) -> None:
        """`ask_confirm` 在非交互/EOF 场景返回 None，等同答否，不能瞎装。"""
        rc = self._run([None, None])
        self.assertEqual(rc, 0)
        self.browse_main.assert_not_called()
        self.gw_install.assert_not_called()

    def test_mixed_answers_only_install_the_yes_one(self) -> None:
        rc = self._run([True, False])
        self.assertEqual(rc, 0)
        self.browse_main.assert_called_once()
        self.gw_install.assert_not_called()

    def test_yes_flag_bypasses_confirm_entirely(self) -> None:
        with mock.patch("lib.ui.ask_confirm") as ask, \
             mock.patch("lib.browse_install.main", return_value=0) as browse_main, \
             mock.patch("lib.graphwatch.GraphwatchCli.install", return_value=0) as gw_install, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = self.cli.install(yes=True)
        self.assertEqual(rc, 0)
        ask.assert_not_called()
        browse_main.assert_called_once()
        gw_install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
