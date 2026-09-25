"""lazyhelp CLI 与 skills_help 补测：子命令输出、裸跑入口、--skills 文案生成。

不跑真的 gradle / npm，不装任何东西：外部命令一律 patch。
"""

import io
import pathlib
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import skills_help  # noqa: E402
from lib.cli import lazyhelp as lh  # noqa: E402

FAKE_TOOLS = {"cpd": ("文件", "复制目录"), "kk": ("进程", "杀进程")}


def cli() -> lh.LazyhelpCli:
    c = lh.LazyhelpCli()
    c._r = mock.MagicMock()
    return c


class TestAll(unittest.TestCase):
    """all：标题带工具总数，表格交给 _render_table 渲染。"""

    def test_renders_every_tool(self):
        c = cli()
        with mock.patch("lib.lazyhelp.TOOLS", FAKE_TOOLS), \
             mock.patch.object(lh, "_render_table") as table:
            self.assertEqual(c.all(), 0)
        self.assertIn("共 2 个", c._r.rule.call_args.args[0])
        rows = table.call_args.args[0]
        self.assertEqual(rows, [("cpd", "文件", "复制目录"), ("kk", "进程", "杀进程")])


class TestHelp(unittest.TestCase):
    """help：位置参数全部透传给目标 bin。"""

    def test_forwards_extra_args(self):
        with mock.patch.object(lh, "show_full", return_value=0) as show:
            self.assertEqual(cli().help("cpd", "--verbose", "x"), 0)
        show.assert_called_once_with("cpd", extra_args=["--verbose", "x"])

    def test_returns_target_exit_code(self):
        with mock.patch.object(lh, "show_full", return_value=3):
            self.assertEqual(cli().help("cpd"), 3)


class TestList(unittest.TestCase):
    """list：一行一个工具名，走 stdout。"""

    def test_prints_one_name_per_line(self):
        out = io.StringIO()
        with mock.patch.object(lh, "_all_bins", return_value=["cpd", "kk"]), \
             mock.patch("sys.stdout", out):
            self.assertEqual(cli().list(), 0)
        self.assertEqual(out.getvalue(), "cpd\nkk\n")


class TestEnv(unittest.TestCase):
    """env：把运行环境打成一张 kv 表。"""

    def test_reports_ai_env_and_python(self):
        c = cli()
        c._r.minimal = False
        with mock.patch.dict("os.environ", {"SCRIPTS_LOG_LEVEL": "DEBUG"}):
            self.assertEqual(c.env(), 0)
        rows = c._r.kv.call_args.args[1]
        self.assertEqual(rows["日志级别"], "DEBUG")
        self.assertEqual(rows["输出模式"], "美化（用户终端）")
        self.assertIn("ai-shell-env", rows)
        self.assertIn("SCRIPTS_LOG_LEVEL=DEBUG", rows["SCRIPTS_*"])

    def test_marks_minimal_mode(self):
        c = cli()
        c._r.minimal = True
        c.env()
        self.assertEqual(c._r.kv.call_args.args[1]["输出模式"], "极简（AI 环境）")


class TestIdea(unittest.TestCase):
    """idea：目录不在直接报错；mise 不在报另一句。"""

    def test_missing_project_dir_exits_2(self):
        c = cli()
        with mock.patch.object(pathlib.Path, "is_dir", return_value=False):
            self.assertEqual(c.idea(), 2)
        self.assertIn("未找到 IntelliJ 插件目录", c._r.err.call_args.args[0])

    def test_missing_mise_exits_1(self):
        c = cli()
        with mock.patch.object(pathlib.Path, "is_dir", return_value=True), \
             mock.patch("subprocess.call", side_effect=FileNotFoundError):
            self.assertEqual(c.idea(), 1)
        self.assertIn("找不到 mise", c._r.err.call_args.args[0])

    def test_success_reports_zip_path(self):
        c = cli()
        with mock.patch.object(pathlib.Path, "is_dir", return_value=True), \
             mock.patch("subprocess.call", return_value=0), \
             mock.patch.object(pathlib.Path, "glob",
                               return_value=[pathlib.Path("/tmp/lazy-git-1.zip")]):
            self.assertEqual(c.idea(), 0)
        self.assertEqual(c._r.kv.call_args.args[1]["zip"], "/tmp/lazy-git-1.zip")

    def test_gradle_failure_propagates(self):
        c = cli()
        with mock.patch.object(pathlib.Path, "is_dir", return_value=True), \
             mock.patch("subprocess.call", return_value=7):
            self.assertEqual(c.idea(), 7)


class TestBrowserExtensions(unittest.TestCase):
    """browser_extensions：只认同时有 package.json 和 src/manifest.json 的目录。"""

    def test_missing_root_is_empty(self):
        self.assertEqual(lh.browser_extensions(pathlib.Path("/nope/extensions")), [])

    def test_only_complete_extensions_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            good = root / "browse"
            (good / "src").mkdir(parents=True)
            (good / "package.json").write_text("{}")
            (good / "src" / "manifest.json").write_text("{}")
            (root / "shared").mkdir()
            (root / "shared" / "package.json").write_text("{}")  # 没有 manifest，不算扩展
            self.assertEqual(lh.browser_extensions(root), [good])


class TestMain(unittest.TestCase):
    """main：裸跑直接渲染速查表并退出，不走 fire 总览。"""

    def test_bare_call_renders_table_and_exits_0(self):
        with mock.patch.object(sys, "argv", ["lazyhelp"]), \
             mock.patch("lib.lazyhelp.TOOLS", FAKE_TOOLS), \
             mock.patch.object(lh, "_render_table") as table, \
             mock.patch.object(lh, "run_cli") as run_cli, \
             self.assertRaises(SystemExit) as ctx:
            lh.main()
        self.assertEqual(ctx.exception.code, 0)
        table.assert_called_once()
        run_cli.assert_not_called()

    def test_with_subcommand_hands_off_to_fire(self):
        with mock.patch.object(sys, "argv", ["lazyhelp", "list"]), \
             mock.patch.object(lh, "run_cli") as run_cli:
            lh.main()
        run_cli.assert_called_once()
        self.assertIsInstance(run_cli.call_args.args[0], lh.LazyhelpCli)


class TestRenderSkills(unittest.TestCase):
    """render_skills：--skills 输出的三段结构 + lazyhelp 独有的工具目录。"""

    def test_unknown_command_without_description_gets_fallback_line(self):
        got = skills_help.render_skills("没这个命令")
        self.assertIn("- 需要本命令的文档化行为；人类用法细节看 `--help`。", got)

    def test_description_is_used_when_no_curated_skills(self):
        got = skills_help.render_skills("没这个命令", "做某件事")
        self.assertIn("概述：", got)
        self.assertIn("- 做某件事", got)

    def test_lazyhelp_appends_the_tool_catalog(self):
        with mock.patch("lib.lazyhelp.TOOLS", FAKE_TOOLS), \
             mock.patch("lib.lazyhelp.CATEGORIES_ORDER", ["文件", "进程", "空分类"]):
            got = skills_help.render_skills("lazyhelp")
        self.assertIn("工具目录（什么场景用什么工具）：", got)
        self.assertIn("- cpd — 复制目录（详情: `cpd --skills`）", got)
        self.assertIn("进程:", got)
        self.assertNotIn("空分类", got)  # 没有工具的分类整段跳过

    def test_other_commands_have_no_catalog(self):
        self.assertNotIn("工具目录", skills_help.render_skills("cpd"))


if __name__ == "__main__":
    unittest.main()
