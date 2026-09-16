"""lazyhelp 的 fire CLI 层（`LazyhelpCli`）测试：目前只有 `install` 子命令。

`tests/test_lazyhelp.py` 覆盖的是 `lib/lazyhelp.py` 那层域函数（TOOLS 注册表、
`_render_table`、`main()` 的分发）；这里测的是 `lib/cli/lazyhelp.py` 里薄的
fire 包装——`install` 转调 `browse install` + `graphwatch install` 并汇总
退出码。两边各自的安装逻辑都是假的，不真的跑 native messaging / launchd。
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
    def setUp(self) -> None:
        self.cli = LazyhelpCli()
        self.cli._r = mock.MagicMock()

    def _run(self, browse_rc: int, graphwatch_rc: int) -> int:
        with mock.patch("lib.browse_install.main", return_value=browse_rc) as browse_main, \
             mock.patch("lib.graphwatch.GraphwatchCli.install", return_value=graphwatch_rc) as gw_install, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = self.cli.install()
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


if __name__ == "__main__":
    unittest.main()
