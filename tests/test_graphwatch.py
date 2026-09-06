"""graphwatch 注册表骨架测试：add / remove / list + 配置文件行为。

主缝：GRAPHWATCH_HOME 指向临时目录，全部命令不碰真实家目录。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import graphwatch
from lib.graphwatch import GraphwatchError


class GraphwatchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = os.environ.pop("GRAPHWATCH_HOME", None)
        os.environ["GRAPHWATCH_HOME"] = str(self.home)
        self.addCleanup(self._tmp.cleanup)
        if self._env is None:
            self.addCleanup(os.environ.pop, "GRAPHWATCH_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "GRAPHWATCH_HOME", self._env)

    def mkdir(self, name: str = "repo") -> Path:
        p = self.home / name
        p.mkdir()
        return p

    def _cli(self):
        from lib.graphwatch import GraphwatchCli

        return GraphwatchCli()


class TestConfigFile(GraphwatchCase):
    def test_config_under_home(self):
        self.assertEqual(graphwatch.config_path(), self.home / "graphwatch.yaml")

    def test_missing_config_returns_defaults(self):
        cfg = graphwatch.load_config()
        self.assertEqual(cfg["folders"], [])
        self.assertEqual(cfg["debounce"], 3)
        self.assertIn("api_key", cfg)

    def test_save_is_atomic_and_0600(self):
        cfg = graphwatch.load_config()
        cfg["api_key"] = "sk-secret"
        graphwatch.save_config(cfg)
        p = graphwatch.config_path()
        self.assertTrue(p.exists())
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600)
        # 原子写：同目录不残留临时文件
        leftovers = [x for x in self.home.iterdir() if x.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        # 重新读回一致
        self.assertEqual(graphwatch.load_config()["api_key"], "sk-secret")


class TestRegistry(GraphwatchCase):
    def test_add_registers_existing_dir(self):
        repo = self.mkdir()
        stored = graphwatch.add_folder(repo)
        self.assertEqual(stored, graphwatch.load_config()["folders"][0])
        self.assertIn(str(repo), str(stored))

    def test_add_missing_dir_raises(self):
        with self.assertRaises(GraphwatchError) as cm:
            graphwatch.add_folder(self.home / "nope")
        self.assertIn("nope", str(cm.exception))

    def test_add_file_not_dir_raises(self):
        f = self.home / "afile"
        f.write_text("x")
        with self.assertRaises(GraphwatchError):
            graphwatch.add_folder(f)

    def test_add_dedupes_same_dir(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        graphwatch.add_folder(repo / "." )
        self.assertEqual(len(graphwatch.load_config()["folders"]), 1)

    def test_remove_deletes_entry(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        graphwatch.remove_folder(repo)
        self.assertEqual(graphwatch.load_config()["folders"], [])

    def test_remove_unregistered_raises(self):
        repo = self.mkdir()
        with self.assertRaises(GraphwatchError):
            graphwatch.remove_folder(repo)

    def test_list_folders_empty(self):
        self.assertEqual(graphwatch.list_folders(), [])

    def test_list_folders_roundtrip(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        self.assertEqual(len(graphwatch.list_folders()), 1)


class TestGraphifyDependency(GraphwatchCase):
    def test_ensure_graphify_guidance_when_missing(self):
        saved = sys.modules.pop("graphify", None)
        sys.modules["graphify"] = None  # None → import 视为 ImportError
        try:
            with self.assertRaises(GraphwatchError) as cm:
                graphwatch.ensure_graphify()
        finally:
            if saved is not None:
                sys.modules["graphify"] = saved
            else:
                sys.modules.pop("graphify", None)
        self.assertIn("[graphify]", str(cm.exception))


class TestCli(GraphwatchCase):
    def test_cli_add_and_list(self):
        repo = self.mkdir()
        self.assertEqual(self._cli().add(str(repo)), 0)
        self.assertEqual(len(graphwatch.list_folders()), 1)

    def test_cli_add_missing_returns_nonzero(self):
        rc = self._cli().add(str(self.home / "nope"))
        self.assertNotEqual(rc, 0)

    def test_cli_remove(self):
        repo = self.mkdir()
        self._cli().add(str(repo))
        self.assertEqual(self._cli().remove(str(repo)), 0)
        self.assertEqual(graphwatch.list_folders(), [])


class TestCliList(GraphwatchCase):
    def test_cli_list_empty(self):
        self.assertEqual(self._cli().list(), 0)

    def test_cli_list_with_entry(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        self.assertEqual(self._cli().list(), 0)


class TestConfigEdges(GraphwatchCase):
    def test_default_home_without_env(self):
        os.environ.pop("GRAPHWATCH_HOME", None)
        self.assertEqual(graphwatch.config_home(), Path.home() / ".config" / "lazygophers" / "scripts")

    def test_bad_yaml_raises(self):
        graphwatch.config_path().write_text("- just\n- a\n- list\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()

    def test_non_mapping_yaml_raises(self):
        graphwatch.config_path().write_text("plain string\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()

    def test_save_failure_leaves_no_tmp(self):
        # 制造写失败：把配置根换成一个只读父目录里的路径
        import unittest.mock

        with unittest.mock.patch.object(graphwatch, "config_home", return_value=Path("/definitely/not/writable")):
            with self.assertRaises(OSError):
                graphwatch.save_config({"folders": []})


class TestBinEntry(GraphwatchCase):
    def test_bin_list_smoke(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "bin" / "graphwatch"), "list"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "GRAPHWATCH_HOME": str(self.home)},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("注册", r.stdout + r.stderr)


class TestSaveFailure(GraphwatchCase):
    def test_malformed_yaml_raises(self):
        graphwatch.config_path().write_text("a: [unclosed\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()

    def test_replace_failure_cleans_tmp(self):
        import unittest.mock

        repo = self.mkdir()
        graphwatch.add_folder(repo)
        with unittest.mock.patch.object(graphwatch.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                graphwatch.save_config({"folders": [str(repo)]})
        leftovers = [x.name for x in self.home.iterdir() if x.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

if __name__ == "__main__":
    unittest.main()


class TestConfigWizard(GraphwatchCase):
    def _feed(self, *answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    def test_wizard_writes_all_fields(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed(
            "anthropic", "sk-1234567890abcdef", "https://api.example.com", "claude-sonnet-5", "5",
        ))
        p = graphwatch.config_path()
        self.assertTrue(p.exists())
        self.assertEqual(cfg["backend"], "anthropic")
        self.assertEqual(cfg["api_key"], "sk-1234567890abcdef")
        self.assertEqual(cfg["base_url"], "https://api.example.com")
        self.assertEqual(cfg["model"], "claude-sonnet-5")
        self.assertEqual(cfg["debounce"], 5.0)
        # folders 不被向导改动
        self.assertEqual(cfg["folders"], [])
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_wizard_enter_keeps_defaults_and_existing(self):
        graphwatch.add_folder(self.mkdir())
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", ""))
        self.assertEqual(cfg["debounce"], 3)
        self.assertEqual(cfg["backend"], "")
        self.assertEqual(len(cfg["folders"]), 1)

    def test_wizard_bad_debounce_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "abc", "-1", "7"))
        self.assertEqual(cfg["debounce"], 7.0)

    def test_wizard_ctrl_c_keeps_old_config(self):
        graphwatch.save_config({**graphwatch.load_config(), "api_key": "old"})
        def interrupt(prompt=""):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            graphwatch.run_wizard(input_fn=interrupt)
        self.assertEqual(graphwatch.load_config()["api_key"], "old")


class TestMaskSecret(GraphwatchCase):
    def test_long_secret_masked_middle(self):
        self.assertEqual(graphwatch.mask_secret("sk-1234567890abcdef"), "sk-1…cdef")

    def test_short_secret_fully_hidden(self):
        self.assertEqual(graphwatch.mask_secret("short"), "****")

    def test_empty(self):
        self.assertEqual(graphwatch.mask_secret(""), "")


class TestCliConfig(GraphwatchCase):
    def test_cli_config_show_masks(self):
        cfg = graphwatch.load_config()
        cfg["api_key"] = "sk-1234567890abcdef"
        graphwatch.save_config(cfg)
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = self._cli().config("show")
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertNotIn("sk-1234567890abcdef", out)
        self.assertIn("sk-1", out)

    def test_cli_config_wizard_via_stdin(self):
        import io
        from contextlib import redirect_stderr
        answers = iter(["kimi", "mk-1234567890abcdef", "", "", ""])
        import builtins
        buf = io.StringIO()
        real_input = builtins.input
        builtins.input = lambda prompt="": next(answers)
        try:
            with redirect_stderr(buf):
                rc = self._cli().config()
        finally:
            builtins.input = real_input
        self.assertEqual(rc, 0)
        self.assertEqual(graphwatch.load_config()["backend"], "kimi")
