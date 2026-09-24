"""lazyhelp 的 fire CLI 层测试。

`install` 自动发现全部浏览器扩展，逐个构建，再安装 graphwatch；这里用假的
构建器和安装器验证分发、确认与退出码，不改真实浏览器或系统服务。
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli.lazyhelp import LazyhelpCli, browser_extensions

EXTENSIONS = [pathlib.Path("/extensions/browse"), pathlib.Path("/extensions/toolbox"), pathlib.Path("/extensions/viewer")]


class TestBrowserExtensions(unittest.TestCase):
    def test_discovers_only_buildable_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name in ("browse", "toolbox", "viewer"):
                (root / name / "src").mkdir(parents=True)
                (root / name / "package.json").touch()
                (root / name / "src" / "manifest.json").touch()
            (root / "shared").mkdir()
            (root / "reports").mkdir()

            self.assertEqual([path.name for path in browser_extensions(root)], ["browse", "toolbox", "viewer"])

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(browser_extensions(pathlib.Path("/definitely/missing/extensions")), [])


class TestIdeaBuild(unittest.TestCase):
    def test_idea_runs_mise_managed_gradle(self) -> None:
        cli = LazyhelpCli()
        cli._r = mock.MagicMock()
        import subprocess

        with mock.patch.object(subprocess, "call", return_value=0) as call:
            self.assertEqual(cli.idea(), 0)
        command = call.call_args.args[0]
        self.assertEqual(command[:3], ["mise", "exec", "--"])
        self.assertEqual(command[-1], "buildPlugin")
        self.assertIn("idea-plugins/lazy-git", command[5])
        # 成功后必须把产物路径报给用户（zip 是数据，不是过程叙述）
        cli._r.kv.assert_called_once()
        self.assertIn(".zip", cli._r.kv.call_args.args[1]["zip"])

    def test_idea_reports_missing_mise(self) -> None:
        cli = LazyhelpCli()
        cli._r = mock.MagicMock()
        import subprocess

        with mock.patch.object(subprocess, "call", side_effect=FileNotFoundError):
            self.assertEqual(cli.idea(), 1)
        cli._r.err.assert_called_once()


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = LazyhelpCli()
        self.cli._r = mock.MagicMock()

    def run_install(self, *, answers=None, browse_rc=0, graphwatch_rc=0, build_error=None, yes=False) -> int:
        build_effect = build_error if build_error is not None else lambda path: path / "dist"
        with mock.patch("lib.cli.lazyhelp.browser_extensions", return_value=EXTENSIONS), \
             mock.patch("lib.browse_install.main", return_value=browse_rc) as browse_main, \
             mock.patch("lib.browse_install.build_extension", side_effect=build_effect) as build, \
             mock.patch("lib.graphwatch.GraphwatchCli.install", return_value=graphwatch_rc) as graphwatch_install, \
             mock.patch("lib.ui.ask_confirm", side_effect=answers or []) as ask, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = self.cli.install(yes=yes)
        self.browse_main = browse_main
        self.build = build
        self.graphwatch_install = graphwatch_install
        self.ask = ask
        return result


class TestInstall(InstallCase):
    def test_yes_installs_every_extension_and_graphwatch(self) -> None:
        self.assertEqual(self.run_install(yes=True), 0)
        self.browse_main.assert_called_once_with(["browse install", "--no-wait"])
        self.assertEqual([call.args[0].name for call in self.build.call_args_list], ["toolbox", "viewer"])
        self.graphwatch_install.assert_called_once()
        self.ask.assert_not_called()

    def test_any_failure_makes_result_nonzero_without_stopping(self) -> None:
        self.assertEqual(self.run_install(yes=True, browse_rc=1, graphwatch_rc=1, build_error=OSError("broken")), 1)
        self.browse_main.assert_called_once()
        self.assertEqual(self.build.call_count, 2)
        self.graphwatch_install.assert_called_once()


class TestConfirmGate(InstallCase):
    def test_each_extension_and_graphwatch_has_own_confirmation(self) -> None:
        self.assertEqual(self.run_install(answers=[True, True, True, True]), 0)
        self.assertEqual(self.ask.call_count, 4)
        self.browse_main.assert_called_once()
        self.assertEqual(self.build.call_count, 2)
        self.graphwatch_install.assert_called_once()

    def test_declined_items_are_skipped_without_failure(self) -> None:
        self.assertEqual(self.run_install(answers=[False, False, False, False]), 0)
        self.browse_main.assert_not_called()
        self.build.assert_not_called()
        self.graphwatch_install.assert_not_called()

    def test_missing_answers_are_skipped_without_failure(self) -> None:
        self.assertEqual(self.run_install(answers=[None, None, None, None]), 0)
        self.browse_main.assert_not_called()
        self.build.assert_not_called()
        self.graphwatch_install.assert_not_called()

    def test_mixed_answers_install_only_selected_items(self) -> None:
        self.assertEqual(self.run_install(answers=[False, True, False, True]), 0)
        self.browse_main.assert_not_called()
        self.build.assert_called_once_with(pathlib.Path("/extensions/toolbox"))
        self.graphwatch_install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
