"""gitwf / cicd / completions / lazyhelp 的剩余分支补测。

12 个 merge_*/push_* 薄壳、cicd 的裸跑入口、completions 的解析兜底、
lazyhelp 的装包退路与 AI 极简表格。不跑真 git、不起子进程。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import completions, lazyhelp  # noqa: E402
from lib.cli import cicd as cicd_cli  # noqa: E402
from lib.cli import gitwf  # noqa: E402


class TestGitwfEntries(unittest.TestCase):
    """12 个入口各自锁死 (命令名, 动作, 目标分支) 三元组。"""

    ENTRIES = {
        "merge_canary": ("merge", "canary"), "merge_dev": ("merge", "dev"),
        "merge_develop": ("merge", "develop"), "merge_master": ("merge", "master"),
        "merge_test": ("merge", "test"), "push_canary": ("push", "canary"),
        "push_dev": ("push", "dev"), "push_develop": ("push", "develop"),
        "push_master": ("push", "master"), "push_test": ("push", "test"),
    }

    def _spawned(self, entry_name: str, argv: list[str]):
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(gitwf, "run_cli") as run_cli:
            getattr(gitwf, entry_name)()
        return run_cli.call_args.args[0]

    def test_fixed_target_entries(self):
        for name, (action, target) in self.ENTRIES.items():
            with self.subTest(entry=name):
                cli = self._spawned(name, [name])
                self.assertEqual((cli._name, cli._action, cli._target), (name, action, target))

    def test_branch_entries_take_the_branch_from_argv(self):
        for name, action in (("merge_branch", "merge"), ("push_branch", "push")):
            with self.subTest(entry=name):
                cli = self._spawned(name, [name, "feature/x"])
                self.assertEqual((cli._action, cli._target), (action, "feature/x"))


class TestPopBranchArg(unittest.TestCase):
    """_pop_branch_arg：第一个非 - 开头的参数就是分支名。"""

    def _pop(self, argv: list[str]) -> str:
        with mock.patch.object(sys, "argv", list(argv)):
            return gitwf._pop_branch_arg("merge_branch")

    def test_pops_the_first_positional(self):
        self.assertEqual(self._pop(["merge_branch", "--debug", "feature/x", "here"]), "feature/x")

    def test_help_needs_no_branch(self):
        self.assertEqual(self._pop(["merge_branch", "--help"]), "")

    def test_missing_branch_exits_2(self):
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr"):
            self._pop(["merge_branch"])
        self.assertEqual(ctx.exception.code, 2)


class TestCicdMain(unittest.TestCase):
    """cicd main：裸跑补 --skills，其余交给 fire。"""

    def test_bare_call_appends_skills(self):
        with mock.patch.object(sys, "argv", ["cicd"]), \
             mock.patch.object(cicd_cli, "run_cli") as run_cli:
            cicd_cli.main()
            self.assertEqual(sys.argv, ["cicd", "--skills"])
        run_cli.assert_called_once()

    def test_subcommand_argv_untouched(self):
        with mock.patch.object(sys, "argv", ["cicd", "status"]), \
             mock.patch.object(cicd_cli, "run_cli"):
            cicd_cli.main()
            self.assertEqual(sys.argv, ["cicd", "status"])


class TestCompletions(unittest.TestCase):
    """completions：子命令抽取的兜底路径（读不出来就给空，不炸）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def write(self, name: str, text: str) -> pathlib.Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_bin_dir_points_at_the_repo_bin(self):
        got = completions.bin_dir()
        self.assertEqual(got.name, "bin")
        self.assertTrue(got.is_dir())

    def test_underscore_attributes_get_both_spellings(self):
        script = self.write("demo.py", "class DemoCli:\n"
                                       "    def __init__(self):\n"
                                       "        self.user_group = 1\n")
        self.assertEqual(completions.subcommands(script), ["user-group", "user_group"])

    def test_broken_source_yields_no_subcommands(self):
        self.assertEqual(completions.subcommands(self.write("bad.py", "def (:\n")), [])

    def test_impl_path_falls_back_on_broken_source(self):
        script = self.write("bad2.py", "def (:\n")
        self.assertEqual(completions.impl_path(script), script)

    def test_impl_path_falls_back_without_a_cli_import(self):
        script = self.write("plain.py", "import os\n")
        self.assertEqual(completions.impl_path(script), script)

    def test_impl_path_resolves_a_thin_shell(self):
        script = self.write("shell.py", "from lib.cli.loop import main\n")
        self.assertEqual(completions.impl_path(script).name, "loop.py")


class TestLazyhelpLib(unittest.TestCase):
    """lib/lazyhelp.py：装成包之后没有 bin/ 时的退路 + AI 极简表格。"""

    def test_all_bins_falls_back_to_the_registry(self):
        with mock.patch.object(lazyhelp, "_bin_dir", return_value=None):
            self.assertEqual(lazyhelp._all_bins(), sorted(lazyhelp.TOOLS))

    def test_render_table_is_tsv_in_ai_env(self):
        rows = [("cpd", "文件", "复制目录")]
        out = []
        with mock.patch.dict("os.environ", {"CLAUDECODE": "1"}), \
             mock.patch("lib.ui.print_tsv", side_effect=lambda h, r: out.append((h, r))):
            lazyhelp._render_table(rows, mock.MagicMock())
        self.assertEqual(out, [(["tool", "category", "description"], rows)])

    def test_render_table_without_rows_prints_nothing(self):
        r = mock.MagicMock()
        lazyhelp._render_table([], r)
        r.status_table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
