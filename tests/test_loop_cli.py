"""loop CLI 层测试：count/命令切分、命令规范化、三个子命令的分派与返回码。

`lib.loop.run_loop` 一律 mock —— 这里只验 CLI 怎么解析参数、怎么调它。
"""

from __future__ import annotations

import pathlib
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli import loop as lc  # noqa: E402


def cli() -> lc.LoopCli:
    c = lc.LoopCli()
    c._r = mock.MagicMock()
    return c


class TestSplitCountCmd(unittest.TestCase):
    """首 token 是数字才当次数，否则整组都是命令。"""

    def test_leading_number_is_count(self):
        self.assertEqual(lc._split_count_cmd(("3", "ls", "-l")), (3, ["ls", "-l"]))

    def test_no_number_means_infinite(self):
        self.assertEqual(lc._split_count_cmd(("ls", "-l")), (None, ["ls", "-l"]))

    def test_negative_number_is_count(self):
        self.assertEqual(lc._split_count_cmd(("-2", "ls")), (-2, ["ls"]))

    def test_empty_args(self):
        self.assertEqual(lc._split_count_cmd(()), (None, []))

    def test_number_only_leaves_empty_cmd(self):
        self.assertEqual(lc._split_count_cmd(("5",)), (5, []))


class TestNormalizeCmd(unittest.TestCase):
    """单元素字符串：含 shell 特殊字符走 sh -c，否则按 shlex 拆。"""

    def test_multi_token_untouched(self):
        self.assertEqual(lc._normalize_cmd(["ls", "-l"]), ["ls", "-l"])

    def test_single_token_untouched(self):
        self.assertEqual(lc._normalize_cmd(["ls"]), ["ls"])

    def test_quoted_string_split_by_shlex(self):
        self.assertEqual(lc._normalize_cmd(["echo 'hello world'"]), ["echo", "hello world"])

    def test_pipe_goes_through_sh(self):
        self.assertEqual(lc._normalize_cmd(["ls | wc -l"]), ["sh", "-c", "ls | wc -l"])

    def test_redirect_goes_through_sh(self):
        self.assertEqual(lc._normalize_cmd(["echo hi > /tmp/x"]), ["sh", "-c", "echo hi > /tmp/x"])


class LoopCase(unittest.TestCase):
    def setUp(self):
        self.run_loop = mock.patch.object(lc, "run_loop", return_value=0).start()
        self.addCleanup(mock.patch.stopall)


class TestRun(LoopCase):
    """run：成功即停语义，count 可省。"""

    def test_missing_cmd_exits_1(self):
        c = cli()
        self.assertEqual(c.run(), 1)
        self.assertIn("loop: 缺少命令", [call.args[0] for call in c._r.err.call_args_list])
        self.run_loop.assert_not_called()

    def test_count_only_without_cmd_exits_1(self):
        self.assertEqual(cli().run("3"), 1)
        self.run_loop.assert_not_called()

    def test_count_and_cmd(self):
        self.assertEqual(cli().run("3", "false", timeout=7), 0)
        self.run_loop.assert_called_once_with(["false"], count=3, force=False, timeout=7)

    def test_without_count_is_unbounded(self):
        cli().run("false")
        self.assertIsNone(self.run_loop.call_args.kwargs["count"])

    def test_shell_string_normalized(self):
        cli().run("2", "false | true")
        self.assertEqual(self.run_loop.call_args.args[0], ["sh", "-c", "false | true"])

    def test_returns_run_loop_exit_code(self):
        self.run_loop.return_value = 3
        self.assertEqual(cli().run("ls"), 3)


class TestBareCall(LoopCase):
    """裸调用 `loop ...` 等同 `loop run ...`。"""

    def test_forwards_to_run(self):
        self.assertEqual(cli()("3", "ls", force=True, timeout=5), 0)
        self.run_loop.assert_called_once_with(["ls"], count=3, force=True, timeout=5)


class TestInfinite(LoopCase):
    """infinite：显式无限模式，没有 count、没有 timeout。"""

    def test_missing_cmd_exits_1(self):
        self.assertEqual(cli().infinite(), 1)
        self.run_loop.assert_not_called()

    def test_passes_infinite_flag(self):
        self.assertEqual(cli().infinite("ls", "-l"), 0)
        self.run_loop.assert_called_once_with(["ls", "-l"], count=None, infinite=True, force=False)

    def test_leading_number_is_part_of_cmd(self):
        # infinite 不做 count 切分，数字就是命令的一部分
        cli().infinite("3", "ls")
        self.assertEqual(self.run_loop.call_args.args[0], ["3", "ls"])

    def test_force_forwarded(self):
        cli().infinite("ls", force=True)
        self.assertIs(self.run_loop.call_args.kwargs["force"], True)


class TestForce(LoopCase):
    """force：跑满次数，force 恒为 True。"""

    def test_missing_cmd_exits_1(self):
        self.assertEqual(cli().force(), 1)
        self.run_loop.assert_not_called()

    def test_always_forces(self):
        self.assertEqual(cli().force("4", "ls", timeout=2), 0)
        self.run_loop.assert_called_once_with(["ls"], count=4, force=True, timeout=2)


class TestMain(unittest.TestCase):
    """main：把 LoopCli 交给 fire。"""

    def test_hands_off_to_fire(self):
        with mock.patch.object(lc, "run_cli") as run_cli:
            lc.main()
        run_cli.assert_called_once()
        self.assertIsInstance(run_cli.call_args.args[0], lc.LoopCli)


if __name__ == "__main__":
    unittest.main()
