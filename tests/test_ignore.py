"""lib/ignore.py：忽略清单的读取 / 匹配 / 向上继承，及批量层集成、lazyhelp ignore。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.batch_git import BatchRunner, CallbackBatchOperation, RepoPlan
from lib.cli_flags import consume_no_ignore, is_ignore_disabled, set_ignore_disabled
from lib.ignore import (
    IGNORE_FILENAME,
    IGNORE_KEYS,
    IgnoreError,
    _matches,
    find_ignore,
    load_ignore_file,
)


def _mk_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def _write_ignore(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / IGNORE_FILENAME
    f.write_text(text, encoding="utf-8")
    return f


class TestLoadIgnoreFile(unittest.TestCase):
    def test_empty_file_gives_empty_dict(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "")
            self.assertEqual(load_ignore_file(f), {})

    def test_valid(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "merge: [a, b]\npush: []\nall: [x/*]\n")
            self.assertEqual(load_ignore_file(f),
                             {"merge": ["a", "b"], "push": [], "all": ["x/*"]})

    def test_null_value_is_empty_list(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "merge:\n")
            self.assertEqual(load_ignore_file(f), {"merge": []})

    def test_unknown_key_fails_closed(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "merg: [a]\n")
            with self.assertRaises(IgnoreError) as ctx:
                load_ignore_file(f)
            self.assertIn("merg", str(ctx.exception))

    def test_non_list_value_fails_closed(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "merge: a\n")
            with self.assertRaises(IgnoreError):
                load_ignore_file(f)

    def test_syntax_error_fails_closed(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "merge: [unclosed\n")
            with self.assertRaises(IgnoreError) as ctx:
                load_ignore_file(f)
            self.assertIn("YAML", str(ctx.exception))

    def test_non_mapping_top_level(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "- a\n- b\n")
            with self.assertRaises(IgnoreError):
                load_ignore_file(f)


class TestMatches(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(_matches(Path("sub"), ["sub"]))

    def test_subtree(self):
        self.assertTrue(_matches(Path("sub/inner/repo"), ["sub"]))

    def test_no_match(self):
        self.assertFalse(_matches(Path("other"), ["sub"]))

    def test_glob_layer_and_deeper(self):
        self.assertTrue(_matches(Path("tmp/a"), ["tmp/*"]))
        self.assertTrue(_matches(Path("tmp/a/b"), ["tmp/*"]))

    def test_glob_does_not_match_base_dir(self):
        self.assertFalse(_matches(Path("tmp"), ["tmp/*"]))

    def test_repo_at_ignore_file_dir(self):
        self.assertTrue(_matches(Path("."), ["."]))


class TestFindIgnore(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()
        _write_ignore(self.root, "push: [sub]\nall: [z]\n")
        _write_ignore(self.root / "sub", "merge: [x]\n")
        for rel in ("sub/x", "sub/y", "other", "z"):
            _mk_repo(self.root / rel)

    def test_own_dir_declares_type(self):
        self.assertEqual(find_ignore(self.root / "sub" / "x", "merge", self.root),
                         self.root / "sub" / IGNORE_FILENAME)

    def test_per_type_falls_through_to_parent(self):
        # sub 的清单只声明 merge；查 push 时跳过它，找到 root 的 push: [sub]
        self.assertEqual(find_ignore(self.root / "sub" / "x", "push", self.root),
                         self.root / IGNORE_FILENAME)

    def test_no_declaring_file_means_not_ignored(self):
        self.assertIsNone(find_ignore(self.root / "sub" / "y", "merge", self.root))
        self.assertIsNone(find_ignore(self.root / "other", "push", self.root))

    def test_all_key_wildcards_every_type(self):
        self.assertEqual(find_ignore(self.root / "z", "switch", self.root),
                         self.root / IGNORE_FILENAME)

    def test_stops_at_scan_root(self):
        # 扫描根以上的清单不生效：root 的 all: [sub2/x] 命中，
        # 但以 root/sub2 为扫描根时向上查找不越界
        root2 = self.root / "bound"
        _write_ignore(root2, "all: [sub2/x]\n")
        _mk_repo(root2 / "sub2" / "x")
        self.assertIsNone(find_ignore(root2 / "sub2" / "x", "push", root2 / "sub2"))
        self.assertEqual(find_ignore(root2 / "sub2" / "x", "push", root2),
                         root2 / IGNORE_FILENAME)


class TestConsumeNoIgnore(unittest.TestCase):
    def setUp(self):
        set_ignore_disabled(False)
        self.addCleanup(set_ignore_disabled, False)

    def test_strips_flag_and_sets_state(self):
        argv = consume_no_ignore(["prog", "--no-ignore", "target"])
        self.assertEqual(argv, ["prog", "target"])
        self.assertTrue(is_ignore_disabled())

    def test_no_flag_is_noop(self):
        argv = consume_no_ignore(["prog", "target"])
        self.assertEqual(argv, ["prog", "target"])
        self.assertFalse(is_ignore_disabled())


class TestBatchIgnoreFilter(unittest.TestCase):
    def setUp(self):
        import lib.notify as notify_mod

        say = notify_mod.is_say_disabled()
        notify_mod.set_say_disabled(True)
        self.addCleanup(notify_mod.set_say_disabled, say)

    def _run(self, ignore_op: str | None, *, disable_ignore: bool = False):
        if disable_ignore:
            set_ignore_disabled(True)
            self.addCleanup(set_ignore_disabled, False)
        seen: list[str] = []

        def _detect(repo, r, root):
            seen.append(repo.name)
            return RepoPlan(status="ok", execute=lambda repo, plan, r, root: ("ok", "done"))

        with TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root / "keep")
            _mk_repo(root / "skipme")
            _write_ignore(root, "merge: [skipme]\n")
            result = BatchRunner().run(CallbackBatchOperation(
                title="忽略清单集成", root=root, confirm=False,
                detect_fn=_detect, ignore_op=ignore_op,
            ))
        return result, seen

    def test_ignored_repo_skips_detect(self):
        result, seen = self._run("merge")
        self.assertEqual(seen, ["keep"])
        self.assertEqual(result.total, 2)
        self.assertEqual([x.name for x in result.succeeded], ["keep"])
        self.assertEqual([x.name for x in result.skipped], ["skipme"])
        self.assertIn("ignored by .lazyscriptsignore", result.skipped[0].detail)

    def test_other_op_not_ignored(self):
        result, seen = self._run("push")
        self.assertEqual(sorted(seen), ["keep", "skipme"])
        self.assertEqual(result.total, 2)

    def test_none_op_disables_filter(self):
        result, seen = self._run(None)
        self.assertEqual(sorted(seen), ["keep", "skipme"])

    def test_no_ignore_escape_hatch(self):
        # --no-ignore 最终落到 set_ignore_disabled(True)；env 是 import 期生效，
        # 单测里直接拨状态开关
        result, seen = self._run("merge", disable_ignore=True)
        self.assertEqual(sorted(seen), ["keep", "skipme"])


class TestLazyhelpIgnore(unittest.TestCase):
    def _cli(self):
        from lib.cli.lazyhelp import LazyhelpCli

        return LazyhelpCli()

    def test_generates_sample_with_all_keys(self):
        with TemporaryDirectory() as td:
            rc = self._cli().ignore(td)
            self.assertEqual(rc, 0)
            f = Path(td) / IGNORE_FILENAME
            self.assertTrue(f.is_file())
            data = load_ignore_file(f)
            self.assertEqual(set(data), set(IGNORE_KEYS))
            self.assertTrue(all(v == [] for v in data.values()))
            self.assertIn("相对本文件所在目录", f.read_text(encoding="utf-8"))

    def test_fills_missing_keys_preserving_existing(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "# 注释要留着\nmerge: [keepme]\n")
            rc = self._cli().ignore(td)
            self.assertEqual(rc, 0)
            text = f.read_text(encoding="utf-8")
            self.assertIn("# 注释要留着\n", text)
            self.assertIn("merge: [keepme]\n", text)
            data = load_ignore_file(f)
            self.assertEqual(set(data), set(IGNORE_KEYS))
            self.assertEqual(data["merge"], ["keepme"])
            self.assertEqual(data["push"], [])

    def test_already_complete_is_noop(self):
        with TemporaryDirectory() as td:
            f = _write_ignore(Path(td), "".join(f"{k}: []\n" for k in IGNORE_KEYS))
            before = f.read_text(encoding="utf-8")
            rc = self._cli().ignore(td)
            self.assertEqual(rc, 0)
            self.assertEqual(f.read_text(encoding="utf-8"), before)

    def test_invalid_existing_file_errors(self):
        with TemporaryDirectory() as td:
            _write_ignore(Path(td), "bogus: [x]\n")
            rc = self._cli().ignore(td)
            self.assertEqual(rc, 1)
            self.assertEqual((Path(td) / IGNORE_FILENAME).read_text(encoding="utf-8"),
                             "bogus: [x]\n")


if __name__ == "__main__":
    unittest.main()
