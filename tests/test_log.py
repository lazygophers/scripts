"""lib/log.py：路径、JSONL 字段、脱敏、异常、轮转、并发、权限、excepthook。"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

from lib import log


def _mp_worker(n: int) -> None:
    for i in range(50):
        log.record("mp", logger="mp", worker=n, seq=i, pad="y" * 30)


class LogCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = pathlib.Path(self._tmp.name) / "scripts.log"
        patch = unittest.mock.patch.dict(os.environ, {"SCRIPTS_LOG": str(self.target)})
        patch.start()
        self.addCleanup(patch.stop)

    def _lines(self) -> list[dict]:
        return [json.loads(line) for line in
                self.target.read_text(encoding="utf-8").splitlines() if line]


class TestPath(LogCase):
    def test_default_under_tempdir(self) -> None:
        import tempfile as _tf
        with unittest.mock.patch.dict(os.environ, {"SCRIPTS_LOG": "", "BROWSE_BRIDGE_LOG": ""},
                                      clear=False):
            os.environ.pop("SCRIPTS_LOG", None)
            os.environ.pop("BROWSE_BRIDGE_LOG", None)
            # gettempdir 只认真实存在的目录（要试探写入），拿本测试的临时目录当 TMPDIR
            with unittest.mock.patch.dict(os.environ, {"TMPDIR": self._tmp.name},
                                          clear=False), \
                 unittest.mock.patch.object(_tf, "tempdir", None):  # gettempdir 有缓存
                self.assertEqual(log.path(),
                                 pathlib.Path(self._tmp.name) / "lazygophers" / "scripts.log")

    def test_scripts_log_override(self) -> None:
        self.assertEqual(log.path(), self.target)

    def test_legacy_browse_bridge_log_still_honored(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"SCRIPTS_LOG": "", "BROWSE_BRIDGE_LOG": "/tmp/x.log"},
                                      clear=False):
            os.environ.pop("SCRIPTS_LOG", None)
            self.assertEqual(log.path(), pathlib.Path("/tmp/x.log"))


class TestJsonlFields(LogCase):
    def test_base_fields_and_extras(self) -> None:
        log.record("cli.start", logger="cpd", src="a", n=3)
        (entry,) = self._lines()
        self.assertEqual(entry["event"], "cli.start")
        self.assertEqual(entry["logger"], "cpd")
        self.assertEqual(entry["level"], "info")
        self.assertIsInstance(entry["at"], float)
        self.assertEqual(entry["pid"], os.getpid())
        self.assertEqual((entry["src"], entry["n"]), ("a", 3))

    def test_sensitive_keys_are_redacted(self) -> None:
        log.record("login", logger="archery", password="p", api_token="t",
                   cookie_jar="c", auth_header="h", hostname="db.example.com")
        (entry,) = self._lines()
        self.assertEqual(entry["password"], log.REDACTED)
        self.assertEqual(entry["api_token"], log.REDACTED)
        self.assertEqual(entry["cookie_jar"], log.REDACTED)
        self.assertEqual(entry["auth_header"], log.REDACTED)
        self.assertEqual(entry["hostname"], "db.example.com")  # 正常字段不受影响

    def test_exception_serialization(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as e:
            log.record("cli.fail", logger="cpd", level="error", exc_info=sys.exc_info())
        (entry,) = self._lines()
        self.assertEqual(entry["level"], "error")
        self.assertEqual(entry["exc_type"], "ValueError")
        self.assertEqual(entry["exc_value"], "boom")
        self.assertIn("test_exception_serialization", entry["traceback"])

    def test_context_name_used_when_logger_omitted(self) -> None:
        log.set_context("merge_master")
        self.addCleanup(log.set_context, "")
        log.record("cli.error", msg="冲突")
        (entry,) = self._lines()
        self.assertEqual(entry["logger"], "merge_master")
        self.assertEqual(entry["msg"], "冲突")  # msg 是保留名，手拼 record 不丢


class TestRotation(LogCase):
    def _small(self) -> None:
        os.environ["SCRIPTS_LOG_MAXBYTES"] = "300"
        self.addCleanup(os.environ.pop, "SCRIPTS_LOG_MAXBYTES", None)

    def test_rotates_and_keeps_three_backups(self) -> None:
        self._small()
        for i in range(40):
            log.record("tick", logger="t", pad="x" * 40)
        self.assertTrue(self.target.is_file())
        self.assertTrue(self.target.with_suffix(".log.1").is_file())
        self.assertTrue(self.target.with_suffix(".log.2").is_file())
        self.assertTrue(self.target.with_suffix(".log.3").is_file())
        self.assertFalse(self.target.with_suffix(".log.4").exists())  # 最多 3 份备份

    def test_no_rotation_under_threshold(self) -> None:
        self._small()
        log.record("tick", logger="t")
        self.assertFalse(self.target.with_suffix(".log.1").exists())


class TestPermissions(LogCase):
    def test_dir_and_files_are_private(self) -> None:
        log.record("tick", logger="t")
        self.assertEqual(self.target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        os.environ["SCRIPTS_LOG_MAXBYTES"] = "300"
        self.addCleanup(os.environ.pop, "SCRIPTS_LOG_MAXBYTES", None)
        for _ in range(40):
            log.record("tick", logger="t", pad="x" * 40)
        backup = self.target.with_suffix(".log.1")
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)


class TestEnvSwitch(LogCase):
    def test_changing_scripts_log_switches_target(self) -> None:
        log.record("a", logger="t")
        other = pathlib.Path(self._tmp.name) / "other.log"
        os.environ["SCRIPTS_LOG"] = str(other)
        log.record("b", logger="t")
        self.assertEqual([e["event"] for e in self._lines()], ["a"])
        with other.open(encoding="utf-8") as handle:
            self.assertEqual([json.loads(line)["event"] for line in handle], ["b"])


class TestWriteFailureNeverRaises(LogCase):
    def test_blocked_target_is_swallowed(self) -> None:
        blocked = pathlib.Path(self._tmp.name) / "as-a-dir"
        blocked.mkdir()
        os.environ["SCRIPTS_LOG"] = str(blocked)
        with unittest.mock.patch.object(logging, "raiseExceptions", False):  # 别往 stderr 倒噪声
            log.record("tick", logger="t")  # 不抛就算过


class TestConcurrency(LogCase):
    def test_multiprocess_writes_do_not_interleave(self) -> None:
        import multiprocessing as mp

        os.environ["SCRIPTS_LOG_MAXBYTES"] = "20000"  # 200 条 × ~135B ≈ 27KB，会转一次
        self.addCleanup(os.environ.pop, "SCRIPTS_LOG_MAXBYTES", None)

        ctx = mp.get_context("spawn")  # macOS 默认即 spawn；显式写出防平台差异
        procs = [ctx.Process(target=_mp_worker, args=(n,)) for n in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)

        # 归并当前文件 + 全部备份：每一行都完整可解析，条数一条不少
        files = [self.target, *[self.target.with_suffix(f".log.{i}") for i in (1, 2, 3)]]
        entries = []
        for f in files:
            if f.is_file():
                entries.extend(json.loads(line)
                               for line in f.read_text(encoding="utf-8").splitlines() if line)
        self.assertEqual(len(entries), 4 * 50)
        self.assertEqual({e["worker"] for e in entries}, {0, 1, 2, 3})


class TestExcepthook(LogCase):
    def setUp(self) -> None:
        super().setUp()
        saved = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", saved)  # 不把 hook 留给后面的用例

    def test_uncaught_exception_is_logged_then_original_runs(self) -> None:
        calls = []
        log.install_excepthook("browse-daemon")
        try:
            raise RuntimeError("kaboom")
        except RuntimeError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        (entry,) = self._lines()
        self.assertEqual(entry["logger"], "browse-daemon")
        self.assertEqual(entry["level"], "critical")
        self.assertEqual(entry["event"], "crash")
        self.assertIn("kaboom", entry["traceback"])

    def test_install_is_idempotent(self) -> None:
        log.install_excepthook("browse-daemon")
        hook = sys.excepthook
        log.install_excepthook("graphwatch-daemon")  # daemon 在测试进程里反复启动
        self.assertIs(sys.excepthook, hook)
        try:
            raise RuntimeError("x")
        except RuntimeError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        (entry,) = self._lines()
        self.assertEqual(entry["logger"], "graphwatch-daemon")  # 不叠链，最近一次启动的赢


class TestReadEntries(LogCase):
    def test_filters_by_logger_and_skips_truncated_lines(self) -> None:
        log.record("e1", logger="browse-daemon")
        log.record("e2", logger="cpd")
        with self.target.open("a", encoding="utf-8") as handle:
            handle.write('{"at": 1, "event": "half\n')
        log.record("e3", logger="browse-daemon")
        self.assertEqual([e["event"] for e in log.read_entries(logger="browse-daemon")],
                         ["e1", "e3"])
        self.assertEqual(len(log.read_entries(limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
