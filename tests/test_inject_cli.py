"""inject 的 CLI 与写盘逻辑测试:rc 块注入 / 更新 / 卸载,三个子命令。

tests/test_inject_completion.py 覆盖 completion 文件生成,tests/test_inject_touch_id.py
覆盖 Touch ID 分支;这里补它们没跑到的部分——rc 文件的四种状态、scripts.sh 的
幂等写入、uninstall 的清理、run/show/uninstall 三个子命令,以及 AI 配置的写入路径。

绝不碰用户 HOME:每个用例都 reload 一份 inject,把模块级路径常量指到
tempfile.TemporaryDirectory(),并把 pathlib.Path.home() 也换成那个临时目录。
"""

from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.test_inject_completion import ReporterStub, load_inject
from tests.test_inject_touch_id import RecordingReporter, _fake_pathlib


class InjectCase(unittest.TestCase):
    """公共脚手架:一份新 inject 模块 + 一个假 HOME。"""

    def setUp(self) -> None:
        self.inject = load_inject()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)

        i = self.inject
        i._SCRIPTS_DIR = self.home / ".config/lazygophers/scripts"
        i._SCRIPTS_SH = i._SCRIPTS_DIR / "scripts.sh"
        i._COMPLETIONS_BASH = i._SCRIPTS_DIR / "completions.bash"
        i._COMPLETIONS_ZSH = i._SCRIPTS_DIR / "completions.zsh"
        i._FISH_CONFIG_DIR = self.home / ".config/fish"
        i._FISH_COMPLETIONS_DIR = i._FISH_CONFIG_DIR / "completions"

        home = mock.patch.object(pathlib.Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        self.r = RecordingReporter()


class TestBuildScriptsSh(InjectCase):
    """scripts.sh 的内容拼装。"""

    def test_ai_base_url_is_exported(self) -> None:
        content = self.inject._build_scripts_sh(pathlib.Path("/tmp/bin"),
                                                ai_base_url="http://127.0.0.1:9894/proxy")
        self.assertIn('export LAZYGOPHERS_SCRIPTS_BASE_URL="http://127.0.0.1:9894/proxy"', content)

    def test_ai_token_is_exported(self) -> None:
        content = self.inject._build_scripts_sh(pathlib.Path("/tmp/bin"), ai_token="free")
        self.assertIn('export LAZYGOPHERS_SCRIPTS_TOKEN="free"', content)

    def test_no_ai_config_means_no_export_lines(self) -> None:
        content = self.inject._build_scripts_sh(pathlib.Path("/tmp/bin"))
        self.assertNotIn("LAZYGOPHERS_SCRIPTS_", content)

    def test_rc_block_is_wrapped_in_markers(self) -> None:
        block = self.inject._build_rc_block()
        self.assertTrue(block.startswith(self.inject._MARKER_BEGIN))
        self.assertIn(self.inject._MARKER_END, block)


class TestReadExistingAiConfig(InjectCase):
    def test_missing_scripts_sh_returns_empty_pair(self) -> None:
        self.assertEqual(self.inject._read_existing_ai_config(), ("", ""))

    def test_file_without_exports_returns_empty_pair(self) -> None:
        self.inject._SCRIPTS_DIR.mkdir(parents=True)
        self.inject._SCRIPTS_SH.write_text('export PATH="/x:$PATH"\n', encoding="utf-8")
        self.assertEqual(self.inject._read_existing_ai_config(), ("", ""))


class TestPromptAiConfig(InjectCase):
    """_prompt_ai_config:首次配置、取消输入、掩码显示。"""

    def test_confirmed_input_is_returned_stripped(self) -> None:
        with mock.patch("lib.ui.ask_confirm", return_value=True), \
             mock.patch("lib.ui.ask_text", side_effect=["  http://api  ", " tok "]):
            url, token = self.inject._prompt_ai_config(ReporterStub())
        self.assertEqual((url, token), ("http://api", "tok"))

    def test_blank_input_becomes_none(self) -> None:
        with mock.patch("lib.ui.ask_confirm", return_value=True), \
             mock.patch("lib.ui.ask_text", side_effect=["", ""]):
            self.assertEqual(self.inject._prompt_ai_config(ReporterStub()), (None, None))

    def test_cancelled_text_input_keeps_existing(self) -> None:
        self.inject._SCRIPTS_DIR.mkdir(parents=True)
        self.inject._SCRIPTS_SH.write_text(
            'export LAZYGOPHERS_SCRIPTS_BASE_URL="http://old"\n'
            'export LAZYGOPHERS_SCRIPTS_TOKEN="abcdef"\n', encoding="utf-8")
        with mock.patch("lib.ui.ask_confirm", return_value=True), \
             mock.patch("lib.ui.ask_text", side_effect=["http://new", None]):
            url, token = self.inject._prompt_ai_config(ReporterStub())
        self.assertEqual((url, token), ("http://old", "abcdef"))

    def test_existing_token_is_masked_in_panel(self) -> None:
        self.inject._SCRIPTS_DIR.mkdir(parents=True)
        self.inject._SCRIPTS_SH.write_text(
            'export LAZYGOPHERS_SCRIPTS_TOKEN="abcdef"\n', encoding="utf-8")
        r = mock.MagicMock()
        with mock.patch("lib.ui.ask_confirm", return_value=False):
            self.inject._prompt_ai_config(r)
        body = r.panel.call_args[0][1]
        self.assertIn("ab****", body)
        self.assertNotIn("abcdef", body)

    def test_first_time_panel_shows_example(self) -> None:
        r = mock.MagicMock()
        with mock.patch("lib.ui.ask_confirm", return_value=False):
            self.inject._prompt_ai_config(r)
        self.assertIn("示例", r.panel.call_args[0][1])


class TestTouchIdRemaining(unittest.TestCase):
    """补 tests/test_inject_touch_id.py 没覆盖的两条:已存在但不含 pam_tid、写入成功。"""

    def _run(self, files: dict, *, confirm: bool = False, returncode: int = 0):
        inject = load_inject()
        r = RecordingReporter()
        proc = mock.Mock(returncode=returncode)
        with mock.patch.object(inject, "pathlib", _fake_pathlib(files)), \
             mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("subprocess.run", return_value=proc) as sub_run, \
             mock.patch("lib.ui.ask_confirm", return_value=confirm):
            inject._setup_touch_id_sudo(r)
        return r, sub_run

    def test_foreign_sudo_local_is_left_alone(self) -> None:
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth include sudo_local\n",
            "/etc/pam.d/sudo_local": "auth sufficient pam_other.so\n",
        })
        sub_run.assert_not_called()
        self.assertIn("不含 pam_tid", " ".join(r.calls))

    def test_successful_write_reports_next_step(self) -> None:
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth include sudo_local\n",
        }, confirm=True, returncode=0)
        sub_run.assert_called_once()
        self.assertIn("Touch ID sudo 已启用", " ".join(r.calls))


class TestWriteScriptsSh(InjectCase):
    def test_writes_file_when_absent(self) -> None:
        self.inject._write_scripts_sh(self.r)
        self.assertIn("export PATH=", self.inject._SCRIPTS_SH.read_text(encoding="utf-8"))

    def test_identical_content_is_skipped(self) -> None:
        self.inject._write_scripts_sh(self.r)
        self.r.calls.clear()
        self.inject._write_scripts_sh(self.r)
        self.assertIn("已是最新", " ".join(self.r.calls))

    def test_changed_ai_config_rewrites(self) -> None:
        self.inject._write_scripts_sh(self.r)
        self.inject._write_scripts_sh(self.r, ai_token="free")
        self.assertIn("free", self.inject._SCRIPTS_SH.read_text(encoding="utf-8"))


class TestWriteCompletionFiles(InjectCase):
    def test_second_run_skips_everything(self) -> None:
        self.inject._write_completion_files(self.r)
        self.r.calls.clear()
        self.inject._write_completion_files(self.r)
        joined = " ".join(self.r.calls)
        self.assertIn("completions.bash 已是最新", joined)
        self.assertIn("*.fish 已是最新", joined)

    def test_stale_fish_file_is_rewritten(self) -> None:
        self.inject._write_completion_files(self.r)
        stale = self.inject._FISH_COMPLETIONS_DIR / "inject.fish"
        stale.write_text("过期内容", encoding="utf-8")
        self.inject._write_completion_files(self.r)
        self.assertIn("complete -c inject", stale.read_text(encoding="utf-8"))


class TestInjectRc(InjectCase):
    """_inject_rc 的四种状态:文件不存在 / 无块 / 块相同 / 块过期。"""

    def _rc(self, text: str | None) -> pathlib.Path:
        rc = self.home / ".zshrc"
        if text is not None:
            rc.write_text(text, encoding="utf-8")
        return rc

    def test_missing_file_is_reported_missing(self) -> None:
        rc = self._rc(None)
        self.assertEqual(self.inject._inject_rc(rc, "块\n", self.r), "missing")

    def test_block_is_appended_to_existing_rc(self) -> None:
        rc = self._rc("export FOO=1\n")
        block = self.inject._build_rc_block()
        self.assertEqual(self.inject._inject_rc(rc, block, self.r), "wrote")
        text = rc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("export FOO=1\n"))
        self.assertIn(self.inject._MARKER_BEGIN, text)

    def test_identical_block_is_skipped(self) -> None:
        block = self.inject._build_rc_block()
        rc = self._rc("export FOO=1\n\n" + block)
        self.assertEqual(self.inject._inject_rc(rc, block, self.r), "skipped")

    def test_outdated_block_is_replaced_not_duplicated(self) -> None:
        old = f"{self.inject._MARKER_BEGIN}\n旧内容\n{self.inject._MARKER_END}\n"
        rc = self._rc("export FOO=1\n\n" + old)
        block = self.inject._build_rc_block()
        self.assertEqual(self.inject._inject_rc(rc, block, self.r), "wrote")
        text = rc.read_text(encoding="utf-8")
        self.assertNotIn("旧内容", text)
        self.assertEqual(text.count(self.inject._MARKER_BEGIN), 1)


class TestBlockHelpers(InjectCase):
    def test_extract_returns_empty_without_markers(self) -> None:
        self.assertEqual(self.inject._extract_block("没有标记"), "")

    def test_strip_returns_text_unchanged_without_markers(self) -> None:
        self.assertEqual(self.inject._strip_block("没有标记"), "没有标记")

    def test_strip_removes_only_the_block(self) -> None:
        block = self.inject._build_rc_block()
        text = "前面\n" + block + "后面\n"
        self.assertEqual(self.inject._strip_block(text), "前面\n后面\n")


class TestUninstall(InjectCase):
    def _install_everything(self) -> pathlib.Path:
        rc = self.home / ".zshrc"
        rc.write_text("export FOO=1\n\n" + self.inject._build_rc_block(), encoding="utf-8")
        self.inject._write_scripts_sh(self.r)
        self.inject._write_completion_files(self.r)
        return rc

    def test_removes_block_and_generated_files(self) -> None:
        rc = self._install_everything()
        r = mock.MagicMock()
        rc_out = self.inject._uninstall(r)
        self.assertEqual(rc_out, 0)
        self.assertEqual(rc.read_text(encoding="utf-8"), "export FOO=1\n\n")
        self.assertFalse(self.inject._SCRIPTS_SH.exists())
        self.assertFalse(self.inject._COMPLETIONS_BASH.exists())
        self.assertFalse(self.inject._COMPLETIONS_ZSH.exists())
        self.assertEqual(list(self.inject._FISH_COMPLETIONS_DIR.glob("*.fish")), [])

    def test_foreign_fish_completions_are_kept(self) -> None:
        # fish 的 completions 目录是公用的，别人装的补全不能被我们连锅端
        self.inject._write_completion_files(self.r)
        foreign = self.inject._FISH_COMPLETIONS_DIR / "kubectl.fish"
        foreign.write_text("# 别人的", encoding="utf-8")
        ours = next(iter(self.inject._FISH_COMPLETIONS_DIR.glob("*.fish")))
        self.inject._uninstall(mock.MagicMock())
        self.assertTrue(foreign.exists())
        self.assertFalse(ours.exists())

    def test_rc_without_block_is_untouched(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_text("export FOO=1\n", encoding="utf-8")
        self.inject._uninstall(mock.MagicMock())
        self.assertEqual(rc.read_text(encoding="utf-8"), "export FOO=1\n")

    def test_uninstall_on_clean_home_is_rc_0(self) -> None:
        self.assertEqual(self.inject._uninstall(mock.MagicMock()), 0)


class TestCli(InjectCase):
    """三个子命令:run / show / uninstall,以及裸调用。"""

    def setUp(self) -> None:
        super().setUp()
        self.reporter = mock.MagicMock()
        p = mock.patch.object(self.inject, "reporter", return_value=self.reporter)
        p.start()
        self.addCleanup(p.stop)
        self.cli = self.inject.InjectCli()

    def _run_cmd(self, fn, *args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = fn(*args)
        return rc, out.getvalue()

    def test_run_writes_everything_and_reports_missing_rc(self) -> None:
        (self.home / ".zshrc").write_text("export FOO=1\n", encoding="utf-8")
        with mock.patch.object(self.inject, "_prompt_ai_config", return_value=("http://api", "tok")), \
             mock.patch.object(self.inject, "_setup_touch_id_sudo"):
            rc, _ = self._run_cmd(self.cli.run)
        self.assertEqual(rc, 0)
        self.assertIn("http://api", self.inject._SCRIPTS_SH.read_text(encoding="utf-8"))
        self.assertIn(self.inject._MARKER_BEGIN,
                      (self.home / ".zshrc").read_text(encoding="utf-8"))
        rows = dict((label, value) for label, value, _style in
                    self.reporter.summary.call_args[0][1])
        self.assertEqual(rows["写入"], ".zshrc")
        self.assertIn(".bashrc", rows["不存在"])

    def test_bare_call_is_run(self) -> None:
        with mock.patch.object(self.cli, "run", return_value=0) as run:
            self.assertEqual(self.cli(), 0)
        run.assert_called_once_with()

    def test_show_prints_plan_without_writing(self) -> None:
        rc, out = self._run_cmd(self.cli.show)
        self.assertEqual(rc, 0)
        self.assertIn("export PATH=", out)
        self.assertIn(self.inject._MARKER_BEGIN, out)
        self.assertFalse(self.inject._SCRIPTS_SH.exists())

    def test_show_marks_which_rc_files_exist(self) -> None:
        (self.home / ".zshrc").write_text("", encoding="utf-8")
        _rc, out = self._run_cmd(self.cli.show)
        self.assertIn("~/.zshrc (存在)", out)
        self.assertIn("~/.bashrc (不存在, 跳过)", out)

    def test_uninstall_subcommand_returns_0(self) -> None:
        rc, _ = self._run_cmd(self.cli.uninstall)
        self.assertEqual(rc, 0)


class TestMain(unittest.TestCase):
    def test_main_hands_a_cli_to_run_cli(self) -> None:
        inject = load_inject()
        with mock.patch.object(inject, "run_cli") as run, \
             mock.patch.object(sys, "argv", ["inject", "show"]):
            inject.main()
        self.assertIsInstance(run.call_args[0][0], inject.InjectCli)


if __name__ == "__main__":
    unittest.main()
