"""lib/cpd.py 的进程内测试。

tests/test_cpd.py 走 subprocess 黑盒，验证的是 bin/cpd 这条命令的外部行为；
这里直接 import lib.cpd 调用各层函数，覆盖参数解析、源解析、-f 校验、
上下文准备（tty / 非 tty 两条分支）和汇总打印这些黑盒够不到的分支。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import cpd  # noqa: E402
from lib.cpd_core import RunCtx, Stats  # noqa: E402


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _plain_ctx(display_base: str) -> RunCtx:
    """不带进度条的上下文，日志走 print_line，便于断言。"""
    return RunCtx(
        checksum=True,
        verify_md5=True,
        log="all",
        display_base=display_base,
        stats=Stats(),
        console=None,
        progress=None,
        task_id=None,
        plain_progress=None,
    )


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)


class TestSmallHelpers(unittest.TestCase):
    def test_expand_path_handles_user_and_env(self) -> None:
        with mock.patch.dict(os.environ, {"CPD_T": "zzz"}):
            self.assertEqual(cpd._expand_path("$CPD_T/a"), "zzz/a")
        self.assertEqual(cpd._expand_path("~"), os.path.expanduser("~"))

    def test_env_flag_defaults_on_and_only_zero_disables(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(cpd._env_flag("CPD_CHECKSUM"))
        with mock.patch.dict(os.environ, {"CPD_CHECKSUM": "0"}):
            self.assertFalse(cpd._env_flag("CPD_CHECKSUM"))
        with mock.patch.dict(os.environ, {"CPD_CHECKSUM": "no"}):
            self.assertTrue(cpd._env_flag("CPD_CHECKSUM"))

    def test_log_level_falls_back_to_changes(self) -> None:
        for raw, want in [("all", "all"), (" QUIET ", "quiet"), ("nonsense", "changes"), ("", "changes")]:
            with mock.patch.dict(os.environ, {"CPD_LOG": raw}):
                self.assertEqual(cpd._log_level(), want)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cpd._log_level(), "changes")

    def test_is_tty_swallows_exceptions(self) -> None:
        broken = mock.Mock()
        broken.isatty.side_effect = ValueError("closed")
        with mock.patch.object(cpd.sys, "stderr", broken):
            self.assertFalse(cpd._is_tty())

    def test_strip_trailing_sep_keeps_root(self) -> None:
        self.assertEqual(cpd._strip_trailing_sep(os.sep), os.sep)
        self.assertEqual(cpd._strip_trailing_sep("/a/b/"), "/a/b")
        self.assertEqual(cpd._strip_trailing_sep("/a/b"), "/a/b")

    def test_eprint_falls_back_to_stderr_without_rich(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(cpd, "rich_console", return_value=None), \
             mock.patch.object(cpd.sys, "stderr", buf):
            cpd._eprint("hello", 1)
        self.assertIn("hello 1", buf.getvalue())

    def test_eprint_uses_rich_console_when_available(self) -> None:
        console = mock.Mock()
        with mock.patch.object(cpd, "rich_console", return_value=console):
            cpd._eprint("hello", "world")
        console.print.assert_called_once()
        self.assertEqual(console.print.call_args[0][0], "hello world")

    def test_die_raises_system_exit_with_code(self) -> None:
        with mock.patch.object(cpd, "_eprint") as ep:
            with self.assertRaises(SystemExit) as cm:
                cpd._die("boom", 3)
        self.assertEqual(cm.exception.code, 3)
        ep.assert_called_once_with("boom")

    def test_usage_mentions_force_flag(self) -> None:
        with mock.patch.object(cpd, "_eprint") as ep:
            cpd._usage()
        self.assertIn("-f", ep.call_args[0][0])


class TestParseCli(unittest.TestCase):
    def test_no_args_prints_usage_and_exits_2(self) -> None:
        with mock.patch.object(cpd, "_usage") as usage:
            with self.assertRaises(SystemExit) as cm:
                cpd._parse_cli(["cpd"])
        self.assertEqual(cm.exception.code, 2)
        usage.assert_called_once()

    def test_help_exits_0(self) -> None:
        for flag in ("-h", "--help"):
            with mock.patch.object(cpd, "_usage"):
                with self.assertRaises(SystemExit) as cm:
                    cpd._parse_cli(["cpd", flag])
            self.assertEqual(cm.exception.code, 0)

    def test_single_arg_is_not_enough(self) -> None:
        with mock.patch.object(cpd, "_usage"):
            with self.assertRaises(SystemExit) as cm:
                cpd._parse_cli(["cpd", "only-src"])
        self.assertEqual(cm.exception.code, 2)

    def test_force_flag_and_last_arg_is_dest(self) -> None:
        force, sources, dst = cpd._parse_cli(["cpd", "-f", "a", "b", "d"])
        self.assertTrue(force)
        self.assertEqual(sources, ["a", "b"])
        self.assertEqual(dst, "d")

    def test_without_force_flag(self) -> None:
        force, sources, dst = cpd._parse_cli(["cpd", "a", "d"])
        self.assertFalse(force)
        self.assertEqual(sources, ["a"])
        self.assertEqual(dst, "d")


class TestResolveSources(TempCase):
    def test_missing_literal_source_dies(self) -> None:
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._resolve_sources([str(self.root / "nope")])

    def test_glob_without_match_dies(self) -> None:
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._resolve_sources([str(self.root / "*.nothing")])

    def test_trailing_sep_marks_copy_contents(self) -> None:
        d = self.root / "src"
        d.mkdir()
        out = cpd._resolve_sources([str(d) + os.sep])
        self.assertEqual(out, [(str(d), True)])

    def test_glob_picks_up_hidden_sibling_pattern(self) -> None:
        _write(self.root / "a.txt")
        _write(self.root / ".a.txt")
        with mock.patch.dict(os.environ, {"CPD_INCLUDE_HIDDEN": "1"}):
            got = {os.path.basename(p) for p, _ in cpd._resolve_sources([str(self.root / "*.txt")])}
        self.assertEqual(got, {"a.txt", ".a.txt"})

    def test_glob_without_hidden_flag_skips_dotfiles(self) -> None:
        _write(self.root / "a.txt")
        _write(self.root / ".a.txt")
        with mock.patch.dict(os.environ, {"CPD_INCLUDE_HIDDEN": "0"}):
            got = {os.path.basename(p) for p, _ in cpd._resolve_sources([str(self.root / "*.txt")])}
        self.assertEqual(got, {"a.txt"})

    def test_symlink_source_is_accepted_even_when_dangling(self) -> None:
        link = self.root / "link"
        os.symlink(str(self.root / "missing"), str(link))
        out = cpd._resolve_sources([str(link)])
        self.assertEqual(out, [(str(link), False)])

    def test_glob_with_hidden_dedups(self) -> None:
        _write(self.root / "a.txt")
        pattern = str(self.root / "*.txt")
        got = cpd._glob_with_hidden(pattern)
        self.assertEqual(got, [str(self.root / "a.txt")])


class TestAugmentHiddenEntries(TempCase):
    def test_adds_dotfiles_when_sources_are_the_full_visible_set(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        _write(src / ".hidden")
        sources = [(str(src / "a"), False), (str(src / "b"), False)]
        cpd._augment_hidden_entries(sources)
        self.assertIn((str(src / ".hidden"), False), sources)

    def test_noop_when_sources_are_a_subset(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        _write(src / ".hidden")
        sources = [(str(src / "a"), False)]
        cpd._augment_hidden_entries(sources)
        self.assertEqual(len(sources), 1)

    def test_noop_when_sources_span_two_parents(self) -> None:
        _write(self.root / "d1" / "a")
        _write(self.root / "d2" / "b")
        sources = [(str(self.root / "d1" / "a"), False), (str(self.root / "d2" / "b"), False)]
        cpd._augment_hidden_entries(sources)
        self.assertEqual(len(sources), 2)

    def test_noop_when_parent_is_gone(self) -> None:
        sources = [(str(self.root / "gone" / "a"), False)]
        cpd._augment_hidden_entries(sources)
        self.assertEqual(len(sources), 1)


class TestResolvePlan(TempCase):
    def test_dest_force_dir_follows_trailing_sep(self) -> None:
        _write(self.root / "a")
        plan = cpd._resolve_plan([str(self.root / "a")], str(self.root / "d") + os.sep, force=False)
        self.assertTrue(plan.dest_force_dir)
        self.assertFalse(plan.force)

    def test_shell_expanded_multi_source_gets_hidden_augmented(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        _write(src / ".h")
        plan = cpd._resolve_plan(
            [str(src / "a"), str(src / "b")], str(self.root / "d") + os.sep, force=False
        )
        names = {os.path.basename(p) for p, _ in plan.sources}
        self.assertEqual(names, {"a", "b", ".h"})

    def test_glob_source_is_not_augmented_again(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / ".h")
        with mock.patch.object(cpd, "_augment_hidden_entries") as aug:
            cpd._resolve_plan([str(src / "*"), str(src / "a")], str(self.root / "d"), force=False)
        aug.assert_not_called()


class TestEntriesSetAndDstRoot(TempCase):
    def test_entries_set_missing_dir_is_empty(self) -> None:
        self.assertEqual(cpd._entries_set(str(self.root / "gone"), include_hidden=True), set())

    def test_entries_set_respects_hidden_flag(self) -> None:
        _write(self.root / "a")
        _write(self.root / ".h")
        self.assertEqual(cpd._entries_set(str(self.root), include_hidden=True), {"a", ".h"})
        self.assertEqual(cpd._entries_set(str(self.root), include_hidden=False), {"a"})

    def test_dst_root_nests_when_dest_is_a_dir(self) -> None:
        got = cpd._compute_single_dir_dst_root(
            src_dir="/tmp/src", dst="/tmp/dst", dest_force_dir=True,
            copy_dir_contents=False, dst_is_dir_before=False,
        )
        self.assertEqual(got, os.path.join("/tmp/dst", "src"))

    def test_dst_root_is_dest_itself_for_contents_copy(self) -> None:
        got = cpd._compute_single_dir_dst_root(
            src_dir="/tmp/src", dst="/tmp/dst", dest_force_dir=True,
            copy_dir_contents=True, dst_is_dir_before=True,
        )
        self.assertEqual(got, "/tmp/dst")

    def test_dst_root_is_dest_itself_when_dest_absent(self) -> None:
        got = cpd._compute_single_dir_dst_root(
            src_dir="/tmp/src", dst="/tmp/dst", dest_force_dir=False,
            copy_dir_contents=False, dst_is_dir_before=False,
        )
        self.assertEqual(got, "/tmp/dst")


class TestForceValidation(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self._ep = mock.patch.object(cpd, "_eprint")
        self._ep.start()
        self.addCleanup(self._ep.stop)

    def test_empty_source_list_dies(self) -> None:
        with self.assertRaises(SystemExit):
            cpd._validate_force_multi_sources(sources=[], include_hidden=True)

    def test_trailing_sep_source_rejected_in_multi_mode(self) -> None:
        with self.assertRaises(SystemExit):
            cpd._validate_force_multi_sources(sources=[("/a/x", True), ("/a/y", False)], include_hidden=True)

    def test_two_parents_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            cpd._validate_force_multi_sources(sources=[("/a/x", False), ("/b/y", False)], include_hidden=True)

    def test_unreadable_src_root_dies(self) -> None:
        missing = self.root / "gone"
        with self.assertRaises(SystemExit):
            cpd._validate_force_multi_sources(
                sources=[(str(missing / "x"), False)], include_hidden=True
            )

    def test_subset_rejected(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        with self.assertRaises(SystemExit):
            cpd._validate_force_multi_sources(sources=[(str(src / "a"), False)], include_hidden=True)

    def test_full_set_returns_src_root(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        got = cpd._validate_force_multi_sources(
            sources=[(str(src / "a"), False), (str(src / "b"), False)], include_hidden=True
        )
        self.assertEqual(got, str(src))

    def test_validate_force_mode_noop_without_force(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        self.assertIsNone(cpd._validate_force_mode(plan, "/d"))

    def test_validate_force_mode_single_source_must_be_a_dir(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), False)], dest="/d", dest_force_dir=False, force=True)
        with self.assertRaises(SystemExit):
            cpd._validate_force_mode(plan, "/d")

    def test_validate_force_mode_single_dir_returns_none(self) -> None:
        d = self.root / "src"
        d.mkdir()
        plan = cpd.CopyPlan(sources=[(str(d), False)], dest="/d", dest_force_dir=False, force=True)
        self.assertIsNone(cpd._validate_force_mode(plan, "/d"))

    def test_validate_force_mode_multi_needs_dir_dest(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        dst = _write(self.root / "dst.txt")
        plan = cpd.CopyPlan(
            sources=[(str(src / "a"), False), (str(src / "b"), False)],
            dest=str(dst), dest_force_dir=False, force=True,
        )
        with self.assertRaises(SystemExit):
            cpd._validate_force_mode(plan, str(dst))


class TestCopySingle(TempCase):
    def test_file_to_existing_dir_keeps_basename(self) -> None:
        src = _write(self.root / "a.txt", b"a")
        dst = self.root / "dst"
        dst.mkdir()
        cpd._copy_single(str(src), str(dst), False, False, _plain_ctx(str(dst)))
        self.assertEqual((dst / "a.txt").read_bytes(), b"a")

    def test_dir_into_dir_nests(self) -> None:
        src = self.root / "src"
        _write(src / "x", b"x")
        dst = self.root / "dst"
        cpd._copy_single(str(src), str(dst), True, False, _plain_ctx(str(dst)))
        self.assertEqual((dst / "src" / "x").read_bytes(), b"x")

    def test_dir_contents_mode_flattens(self) -> None:
        src = self.root / "src"
        _write(src / "x", b"x")
        dst = self.root / "dst"
        cpd._copy_single(str(src), str(dst), True, True, _plain_ctx(str(dst)))
        self.assertEqual((dst / "x").read_bytes(), b"x")

    def test_dir_to_missing_path_creates_it(self) -> None:
        src = self.root / "src"
        _write(src / "x", b"x")
        dst = self.root / "dst"
        cpd._copy_single(str(src), str(dst), False, False, _plain_ctx(str(dst)))
        self.assertEqual((dst / "x").read_bytes(), b"x")

    def test_dir_to_existing_file_dies(self) -> None:
        src = self.root / "src"
        _write(src / "x")
        dst = _write(self.root / "dst.txt")
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._copy_single(str(src), str(dst), False, False, _plain_ctx(str(self.root)))

    def test_file_to_file(self) -> None:
        src = _write(self.root / "a.txt", b"a")
        dst = self.root / "b.txt"
        cpd._copy_single(str(src), str(dst), False, False, _plain_ctx(str(self.root)))
        self.assertEqual(dst.read_bytes(), b"a")


class TestEstimateTotalOps(TempCase):
    def test_files_count_one_each_and_never_below_one(self) -> None:
        a = _write(self.root / "a")
        plan = cpd.CopyPlan(sources=[(str(a), False)], dest="/d", dest_force_dir=False, force=False)
        self.assertGreaterEqual(cpd._estimate_total_ops(plan), 1)

    def test_dir_counts_its_entries(self) -> None:
        src = self.root / "src"
        _write(src / "a")
        _write(src / "b")
        plan = cpd.CopyPlan(sources=[(str(src), False)], dest="/d", dest_force_dir=False, force=False)
        self.assertGreaterEqual(cpd._estimate_total_ops(plan), 2)

    def test_empty_plan_is_clamped_to_one(self) -> None:
        plan = cpd.CopyPlan(sources=[], dest="/d", dest_force_dir=False, force=False)
        self.assertEqual(cpd._estimate_total_ops(plan), 1)


class TestPlainProgress(unittest.TestCase):
    def test_render_then_clear_writes_and_erases(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(cpd.sys, "stderr", buf):
            p = cpd._PlainProgress(total=2)
            p.update_counts(copied=1, skipped=0, bytes_copied=1024)
            p.advance(path="a.txt")
            p.render(force=True)
            p.finish()
        out = buf.getvalue()
        self.assertIn("1/2", out)
        self.assertIn("a.txt", out)
        self.assertTrue(out.endswith("\r"))

    def test_zero_total_is_clamped(self) -> None:
        self.assertEqual(cpd._PlainProgress(total=0).total, 1)

    def test_render_throttles_within_50ms(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(cpd.sys, "stderr", buf):
            p = cpd._PlainProgress(total=10)
            p.render(force=True)
            first = buf.getvalue()
            p.render()  # 距上次不足 50ms，应被节流
        self.assertEqual(buf.getvalue(), first)

    def test_clear_is_noop_before_first_render(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(cpd.sys, "stderr", buf):
            cpd._PlainProgress(total=1).clear()
        self.assertEqual(buf.getvalue(), "")

    def test_long_line_is_truncated_to_terminal_width(self) -> None:
        buf = io.StringIO()
        fake = mock.Mock()
        fake.columns = 40
        with mock.patch.object(cpd.sys, "stderr", buf), \
             mock.patch.object(cpd.shutil, "get_terminal_size", return_value=fake):
            p = cpd._PlainProgress(total=1)
            p.advance(path="x" * 300)
            p.render(force=True)
        self.assertIn("…", buf.getvalue())

    def test_terminal_size_failure_falls_back_to_120(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(cpd.sys, "stderr", buf), \
             mock.patch.object(cpd.shutil, "get_terminal_size", side_effect=OSError("no tty")):
            cpd._PlainProgress(total=1).render(force=True)
        self.assertIn("0/1", buf.getvalue())


class TestPrepareContext(TempCase):
    def test_non_tty_has_no_progress_at_all(self) -> None:
        d = self.root / "src"
        _write(d / "a")
        plan = cpd.CopyPlan(sources=[(str(d), False)], dest=str(self.root / "dst"),
                            dest_force_dir=True, force=False)
        with mock.patch.object(cpd, "_is_tty", return_value=False):
            ctx = cpd._prepare_context(plan)
        self.assertIsNone(ctx.console)
        self.assertIsNone(ctx.progress)
        self.assertIsNone(ctx.plain_progress)
        self.assertEqual(ctx.display_base, str(self.root / "dst"))

    def test_display_base_is_parent_dir_for_file_to_file(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), False)], dest=str(self.root / "b.txt"),
                            dest_force_dir=False, force=False)
        with mock.patch.object(cpd, "_is_tty", return_value=False):
            ctx = cpd._prepare_context(plan)
        self.assertEqual(ctx.display_base, str(self.root))

    def test_tty_without_rich_uses_plain_progress(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), False)], dest=str(self.root / "b.txt"),
                            dest_force_dir=False, force=False)
        buf = io.StringIO()
        with mock.patch.object(cpd, "_is_tty", return_value=True), \
             mock.patch.object(cpd, "rich_console", return_value=None), \
             mock.patch.object(cpd.sys, "stderr", buf):
            ctx = cpd._prepare_context(plan)
        self.assertIsInstance(ctx.plain_progress, cpd._PlainProgress)
        self.assertIsNone(ctx.progress)

    @unittest.skipIf(cpd.Table is None, "未安装 rich")
    def test_tty_with_rich_builds_a_progress_task(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), False)], dest=str(self.root / "b.txt"),
                            dest_force_dir=False, force=False)
        from rich.console import Console

        console = Console(file=io.StringIO(), force_terminal=True)
        with mock.patch.object(cpd, "_is_tty", return_value=True), \
             mock.patch.object(cpd, "rich_console", return_value=console):
            ctx = cpd._prepare_context(plan)
        self.assertIsNotNone(ctx.progress)
        self.assertIsNotNone(ctx.task_id)

    def test_env_flags_reach_the_context(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), False)], dest=str(self.root / "b.txt"),
                            dest_force_dir=False, force=False)
        env = {"CPD_CHECKSUM": "0", "CPD_VERIFY_MD5": "0", "CPD_LOG": "quiet"}
        with mock.patch.dict(os.environ, env), mock.patch.object(cpd, "_is_tty", return_value=False):
            ctx = cpd._prepare_context(plan)
        self.assertFalse(ctx.checksum)
        self.assertFalse(ctx.verify_md5)
        self.assertEqual(ctx.log, "quiet")


class TestPrinting(TempCase):
    def test_plan_without_rich_prints_every_row(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False), ("/b", True)], dest="/d",
                            dest_force_dir=True, force=True)
        ctx = _plain_ctx("/d")
        with mock.patch.object(ctx, "print_line") as pl:
            cpd._print_copy_plan(plan, ctx)
        printed = "\n".join(str(c[0][0]) for c in pl.call_args_list)
        self.assertIn("复制计划", printed)
        self.assertIn("强制覆盖(-f): 开启", printed)
        self.assertIn("源: /a", printed)
        self.assertIn(f"源: /b{os.sep}", printed)

    @unittest.skipIf(cpd.Table is None, "未安装 rich")
    def test_plan_with_rich_prints_two_tables(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        ctx = _plain_ctx("/d")
        ctx.console = mock.Mock()
        cpd._print_copy_plan(plan, ctx)
        self.assertEqual(ctx.console.print.call_count, 2)

    def test_summary_without_rich_lists_all_counters(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        ctx = _plain_ctx("/d")
        ctx.stats.copied_files = 3
        with mock.patch.object(cpd, "_eprint") as ep:
            cpd._print_summary(plan, ctx, 1.5)
        printed = "\n".join(str(c[0][0]) for c in ep.call_args_list)
        self.assertIn("复制完成", printed)
        self.assertIn("已复制文件: 3", printed)
        self.assertIn("耗时: 1.50s", printed)

    def test_summary_finishes_plain_progress_and_updates_rich_progress(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        ctx = _plain_ctx("/d")
        ctx.plain_progress = mock.Mock()
        ctx.progress = mock.Mock()
        ctx.task_id = 7
        with mock.patch.object(cpd, "_eprint"):
            cpd._print_summary(plan, ctx, 0.1)
        ctx.progress.update.assert_called_once()
        ctx.plain_progress.finish.assert_called_once()

    def test_summary_swallows_progress_update_failure(self) -> None:
        plan = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        ctx = _plain_ctx("/d")
        ctx.progress = mock.Mock()
        ctx.progress.update.side_effect = RuntimeError("已关闭")
        ctx.task_id = 1
        with mock.patch.object(cpd, "_eprint"):
            cpd._print_summary(plan, ctx, 0.1)  # 不应抛出


class TestExecuteCopy(TempCase):
    def test_multi_source_dest_must_be_a_dir(self) -> None:
        dst = _write(self.root / "dst.txt")
        plan = cpd.CopyPlan(sources=[("/a", False), ("/b", False)], dest=str(dst),
                            dest_force_dir=False, force=False)
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._execute_multi_source_copy(plan, str(dst), None, _plain_ctx(str(dst)))

    def test_multi_source_force_without_root_is_an_internal_error(self) -> None:
        src = self.root / "src"
        a, b = _write(src / "a"), _write(src / "b")
        dst = self.root / "dst"
        plan = cpd.CopyPlan(sources=[(str(a), False), (str(b), False)], dest=str(dst),
                            dest_force_dir=True, force=True)
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._execute_multi_source_copy(plan, str(dst), None, _plain_ctx(str(dst)))

    def test_multi_source_force_deletes_extras(self) -> None:
        src = self.root / "src"
        a, b = _write(src / "a", b"a"), _write(src / "b", b"b")
        dst = self.root / "dst"
        _write(dst / "stale", b"old")
        plan = cpd.CopyPlan(sources=[(str(a), False), (str(b), False)], dest=str(dst),
                            dest_force_dir=True, force=True)
        cpd._execute_multi_source_copy(plan, str(dst), str(src), _plain_ctx(str(dst)))
        self.assertEqual((dst / "a").read_bytes(), b"a")
        self.assertFalse((dst / "stale").exists())

    def test_single_source_trailing_sep_requires_a_dir(self) -> None:
        f = _write(self.root / "a.txt")
        plan = cpd.CopyPlan(sources=[(str(f), True)], dest=str(self.root / "d"),
                            dest_force_dir=True, force=False)
        with mock.patch.object(cpd, "_eprint"):
            with self.assertRaises(SystemExit):
                cpd._execute_single_source_copy(plan, _plain_ctx(str(self.root)))

    def test_single_source_force_cleans_computed_root(self) -> None:
        src = self.root / "src"
        _write(src / "a", b"a")
        dst = self.root / "dst"
        _write(dst / "src" / "stale", b"old")
        _write(dst / "keep", b"keep")
        plan = cpd.CopyPlan(sources=[(str(src), False)], dest=str(dst),
                            dest_force_dir=True, force=True)
        cpd._execute_single_source_copy(plan, _plain_ctx(str(dst)))
        self.assertEqual((dst / "src" / "a").read_bytes(), b"a")
        self.assertFalse((dst / "src" / "stale").exists())
        self.assertEqual((dst / "keep").read_bytes(), b"keep")

    def test_execute_copy_dispatches_on_source_count(self) -> None:
        one = cpd.CopyPlan(sources=[("/a", False)], dest="/d", dest_force_dir=False, force=False)
        two = cpd.CopyPlan(sources=[("/a", False), ("/b", False)], dest="/d",
                           dest_force_dir=False, force=False)
        ctx = _plain_ctx("/d")
        with mock.patch.object(cpd, "_execute_single_source_copy") as single, \
             mock.patch.object(cpd, "_execute_multi_source_copy") as multi:
            cpd._execute_copy(one, "/d", None, ctx)
            cpd._execute_copy(two, "/d", None, ctx)
        single.assert_called_once()
        multi.assert_called_once()


class TestCopyEndToEnd(TempCase):
    def setUp(self) -> None:
        super().setUp()
        # 端到端跑真实 copy()，但不要 tty 进度条，也不要把摘要打到测试输出里
        self._patches = [
            mock.patch.object(cpd, "_is_tty", return_value=False),
            mock.patch.object(cpd, "_eprint"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_file_to_file_returns_zero(self) -> None:
        src = _write(self.root / "a.txt", b"hello")
        dst = self.root / "b.txt"
        rc = cpd.copy(["cpd", str(src), str(dst)])
        self.assertEqual(rc, 0)
        self.assertEqual(dst.read_bytes(), b"hello")

    def test_dir_contents_into_new_dir(self) -> None:
        src = self.root / "src"
        _write(src / "a", b"a")
        _write(src / "sub" / "b", b"b")
        dst = self.root / "dst"
        rc = cpd.copy(["cpd", str(src) + os.sep, str(dst) + os.sep])
        self.assertEqual(rc, 0)
        self.assertEqual((dst / "a").read_bytes(), b"a")
        self.assertEqual((dst / "sub" / "b").read_bytes(), b"b")

    def test_force_dir_sync_deletes_extras(self) -> None:
        src = self.root / "src"
        _write(src / "a", b"a")
        dst = self.root / "dst"
        _write(dst / "stale", b"old")
        rc = cpd.copy(["cpd", "-f", str(src) + os.sep, str(dst) + os.sep])
        self.assertEqual(rc, 0)
        self.assertEqual((dst / "a").read_bytes(), b"a")
        self.assertFalse((dst / "stale").exists())

    def test_second_run_skips_unchanged_file(self) -> None:
        src = _write(self.root / "a.txt", b"same")
        dst = self.root / "b.txt"
        cpd.copy(["cpd", str(src), str(dst)])
        before = dst.stat().st_mtime_ns
        cpd.copy(["cpd", str(src), str(dst)])
        self.assertEqual(dst.stat().st_mtime_ns, before)

    def test_missing_source_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cpd.copy(["cpd", str(self.root / "nope"), str(self.root / "d")])
        self.assertEqual(cm.exception.code, 1)

    def test_progress_context_manager_is_entered_when_present(self) -> None:
        src = _write(self.root / "a.txt", b"a")
        dst = self.root / "b.txt"
        progress = mock.MagicMock()
        real_prepare = cpd._prepare_context

        def fake_prepare(plan):
            ctx = real_prepare(plan)
            ctx.progress = progress
            ctx.task_id = 0
            return ctx

        with mock.patch.object(cpd, "_prepare_context", side_effect=fake_prepare):
            cpd.copy(["cpd", str(src), str(dst)])
        progress.__enter__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
