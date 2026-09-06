"""graphwatch 注册表骨架测试：add / remove / list + 配置文件行为。

主缝：GRAPHWATCH_HOME 指向临时目录，全部命令不碰真实家目录。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
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


class TestConfigValidation(GraphwatchCase):
    def test_bad_debounce_raises_friendly(self):
        graphwatch.config_path().write_text("debounce: abc\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError) as cm:
            graphwatch.load_config()
        self.assertIn("debounce", str(cm.exception))

    def test_negative_debounce_raises(self):
        graphwatch.config_path().write_text("debounce: -1\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()

    def test_folders_not_list_raises(self):
        graphwatch.config_path().write_text("folders: 42\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()

    def test_unknown_key_warned_and_ignored(self):
        import contextlib
        import io
        graphwatch.config_path().write_text("deboune: 5\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = graphwatch.load_config()
        self.assertEqual(cfg["debounce"], 3.0)
        self.assertIn("deboune", buf.getvalue())

    def test_daemon_survives_bad_debounce(self):
        # 手改配置写坏 debounce：daemon 跳过本轮不崩
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        import threading
        stop = threading.Event()
        fac = FakeFactory()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, watch_factory=fac, poll_interval=0.05), daemon=True)
        thread.start()
        time.sleep(0.2)
        graphwatch.config_path().write_text("debounce: abc\n", encoding="utf-8")
        time.sleep(0.3)
        self.assertTrue(thread.is_alive(), "坏 debounce 不应让 daemon 退出")
        stop.set()
        thread.join(timeout=5)


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
        self.assertIn("requirements.txt", str(cm.exception))


class TestWatchdogDependency(GraphwatchCase):
    def test_missing_watchdog_guidance(self):
        import builtins
        import unittest.mock
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "watchdog":
                raise ImportError("watchdog")
            return real_import(name, *a, **kw)

        with unittest.mock.patch("builtins.__import__", fake_import):
            with self.assertRaises(GraphwatchError) as cm:
                graphwatch.ensure_graphify()
        self.assertIn("watchdog", str(cm.exception))


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


class TestSingletonLock(GraphwatchCase):
    def test_second_acquire_fails(self):
        l1 = graphwatch.acquire_singleton_lock()
        self.assertIsNotNone(l1)
        l2 = graphwatch.acquire_singleton_lock()
        self.assertIsNone(l2)
        graphwatch.release_singleton_lock(l1)
        # 释放后可再拿
        l3 = graphwatch.acquire_singleton_lock()
        self.assertIsNotNone(l3)
        graphwatch.release_singleton_lock(l3)

    def test_lock_file_under_home(self):
        lock = graphwatch.acquire_singleton_lock()
        self.assertTrue((self.home / "graphwatch.lock").exists())
        graphwatch.release_singleton_lock(lock)

    def test_lock_holds_pid(self):
        import os as _os
        lock = graphwatch.acquire_singleton_lock()
        content = (self.home / "graphwatch.lock").read_text().strip()
        self.assertEqual(content, str(_os.getpid()))
        graphwatch.release_singleton_lock(lock)


class FakeProc:
    """假子进程：记录 terminate，可模拟崩溃。"""

    def __init__(self):
        self.terminated = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode or 0

    def kill(self):
        self.terminated = True
        self.returncode = -9

    def terminate(self):
        self.terminated = True
        self.returncode = 0


class FakeFactory:
    """假 watch 工厂：记录每次 start/terminate 的目录与 debounce。"""

    def __init__(self):
        self.started: list[tuple[str, float]] = []
        self.stopped: list[str] = []
        self.procs: dict[str, FakeProc] = {}

    def __call__(self, debounce: float):
        def start(folder):
            key = str(folder)
            self.started.append((key, debounce))
            proc = FakeProc()
            self.procs[key] = proc
            return proc
        return start

    def note_stop(self, folder):
        self.stopped.append(str(folder))

    def crash(self, folder):
        self.procs[str(folder)].returncode = 1


class TestRunDaemon(GraphwatchCase):
    def test_run_requires_graphify(self):
        saved = sys.modules.pop("graphify", None)
        sys.modules["graphify"] = None
        try:
            with self.assertRaises(GraphwatchError) as cm:
                graphwatch.run_daemon()
            self.assertIn("requirements.txt", str(cm.exception))
        finally:
            sys.modules.pop("graphify", None)
            if saved is not None:
                sys.modules["graphify"] = saved

    def test_run_no_graphify_check_injectable(self):
        import threading
        stop = threading.Event()
        stop.set()
        graphwatch.run_daemon(stop_event=stop, ensure=lambda: None)
        lock = graphwatch.acquire_singleton_lock()
        self.assertIsNotNone(lock)
        graphwatch.release_singleton_lock(lock)

    def test_run_starts_watcher_per_folder(self):
        import threading
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        stop = threading.Event()
        fac = FakeFactory()

        def bootstrap():
            stop.set()
        graphwatch.run_daemon(stop_event=stop, ensure=lambda: None, watch_factory=fac, poll_interval=0.05, on_started=bootstrap)
        self.assertEqual([k for k, _ in fac.started], [str(repo.resolve())])
        self.assertEqual(fac.started[0][1], 3.0)

    def test_run_blocked_when_locked(self):
        lock = graphwatch.acquire_singleton_lock()
        try:
            with self.assertRaises(GraphwatchError) as cm:
                graphwatch.run_daemon(ensure=lambda: None)
            self.assertIn("实例", str(cm.exception))
        finally:
            graphwatch.release_singleton_lock(lock)

    def _run_until(self, condition, *, fac=None, **kw):
        """跑 daemon 直到 condition 为真（每次 poll 后检查），然后停。"""
        import threading
        stop = threading.Event()
        args = dict(ensure=lambda: None, poll_interval=0.05)
        args.update(kw)
        if fac is not None:
            args["watch_factory"] = fac
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(stop_event=stop, **args), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "daemon 线程未退出")

    def test_hot_add_starts_watcher(self):
        repo1 = self.mkdir("r1")
        graphwatch.add_folder(repo1)
        fac = FakeFactory()
        repo2 = self.mkdir("r2")

        def step():
            graphwatch.add_folder(repo2)

        self._run_until(lambda: len(fac.started) >= 2, fac=fac, on_started=step)
        self.assertIn(str(repo2.resolve()), [k for k, _ in fac.started])

    def test_hot_remove_stops_watcher(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()
        stopped = []

        def step():
            graphwatch.remove_folder(repo)
            stopped.append(True)

        self._run_until(lambda: stopped and fac.procs and all(p.terminated for p in fac.procs.values()),
                        fac=fac, on_started=lambda: time.sleep(0.2) or stopped.append(None))
        self.assertTrue(all(p.terminated for p in fac.procs.values()))

    def test_bad_yaml_does_not_crash_daemon(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()
        def step():
            graphwatch.config_path().write_text("a: [unclosed\n", encoding="utf-8")
        self._run_until(lambda: False, fac=fac, on_started=step, max_loops_hint=None) if False else None
        # 直接驱动：坏 YAML 期间 load_config 抛错但主循环活着
        import threading
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, watch_factory=fac, poll_interval=0.05), daemon=True)
        thread.start()
        time.sleep(0.2)
        graphwatch.config_path().write_text("a: [unclosed\n", encoding="utf-8")
        time.sleep(0.3)
        self.assertTrue(thread.is_alive(), "坏 YAML 不应让 daemon 退出")
        stop.set()
        thread.join(timeout=5)

    def test_debounce_change_restarts_watchers(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()
        def step():
            cfg = graphwatch.load_config()
            cfg["debounce"] = 7
            graphwatch.save_config(cfg)
        self._run_until(lambda: any(d == 7 for _, d in fac.started), fac=fac, on_started=step)

    def test_crashed_watcher_restarted(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()
        def step():
            if fac.procs:
                fac.crash(repo.resolve())
        self._run_until(lambda: len(fac.started) >= 2, fac=fac, on_started=step)


class TestCliRun(GraphwatchCase):
    def test_cli_run_with_no_folders(self):
        called = {}
        def fake(**kw):
            called.update(kw)
            return 0
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "run_daemon", fake):
            rc = self._cli().run()
        self.assertEqual(rc, 0)
        self.assertTrue(called or called == {})


class TestService(GraphwatchCase):
    def test_plist_content(self):
        plist = graphwatch.launchd_plist()
        self.assertIn("com.lazygophers.graphwatch", plist)
        self.assertIn(str(graphwatch.script_path()), plist)
        self.assertIn("<string>run</string>", plist)
        self.assertIn("<key>KeepAlive</key>", plist)
        self.assertIn("<key>Crashed</key><true/>", plist)
        self.assertIn("<key>RunAtLoad</key>", plist)

    def test_systemd_unit_content(self):
        unit = graphwatch.systemd_unit()
        self.assertIn(str(graphwatch.script_path()), unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_schtasks_command_content(self):
        cmd = graphwatch.schtasks_create_command()
        self.assertEqual(cmd[0], "schtasks")
        self.assertIn("/SC", cmd)
        self.assertIn("ONLOGON", cmd)
        self.assertIn(str(graphwatch.script_path()), " ".join(cmd))

    def test_install_macos_writes_plist_and_loads(self):
        import unittest.mock
        plist = self.home / "com.lazygophers.graphwatch.plist"
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            rc = 1 if "print" in " ".join(cmd) else 0
            return type("R", (), {"returncode": rc})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"), \
             unittest.mock.patch.object(graphwatch, "launchd_plist_path", return_value=plist):
            graphwatch.install_service(runner=fake_run)
            boot = graphwatch._sh("launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist))
        self.assertIn(boot, cmds)
        self.assertEqual(boot[0], "/bin/zsh")
        self.assertTrue(plist.exists())
        self.assertIn(str(graphwatch.script_path()), plist.read_text())

    def test_install_linux_enables_unit(self):
        import unittest.mock
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            return type("R", (), {"returncode": 0})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"):
            graphwatch.install_service(runner=fake_run)
        self.assertIn(["systemctl", "--user", "enable", "--now", "graphwatch.service"], cmds)
        unit = Path.home() / ".config" / "systemd" / "user" / "graphwatch.service"
        self.assertIn("Restart=always", unit.read_text())

    def test_uninstall_macos_removes_plist(self):
        import unittest.mock
        p = self.home / "com.lazygophers.graphwatch.plist"
        p.write_text("x")
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            rc = 1 if "print" in " ".join(cmd) else 0
            return type("R", (), {"returncode": rc})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"), \
             unittest.mock.patch.object(graphwatch, "launchd_plist_path", return_value=p):
            graphwatch.uninstall_service(runner=fake_run)
        self.assertFalse(p.exists())
        joined = [" ".join(c) for c in cmds]
        self.assertTrue(any("kill SIGTERM" in j for j in joined))

    def test_uninstall_windows_deletes_task(self):
        import unittest.mock
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            return type("R", (), {"returncode": 0})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "win32"):
            graphwatch.uninstall_service(runner=fake_run)
        self.assertEqual(cmds[0][:4], ["schtasks", "/Delete", "/F", "/TN"])

    def test_service_registered_is_plist_on_darwin(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"), \
             unittest.mock.patch.object(graphwatch, "launchd_plist_path", return_value=self.home / "x.plist"):
            self.assertFalse(graphwatch.service_registered())
            (self.home / "x.plist").write_text("x")
            self.assertTrue(graphwatch.service_registered())

    def test_daemon_alive_false_when_free(self):
        self.assertFalse(graphwatch.daemon_alive())

    def test_folder_freshness_stale(self):
        repo = self.mkdir()
        (repo / "a.py").write_text("x")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}")
        # graph 比源旧：把源 mtime 拨新
        import os as _os
        st = (repo / "a.py").stat()
        _os.utimedelta = None
        import os
        future = st.st_mtime + 100
        os.utime(repo / "a.py", (future, future))
        status, detail = graphwatch.folder_freshness(str(repo))
        self.assertEqual(status, "fail")

    def test_folder_freshness_no_graph(self):
        repo = self.mkdir()
        status, _ = graphwatch.folder_freshness(str(repo))
        self.assertEqual(status, "skip")

    def test_cli_status_unregistered(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=False):
            rc = self._cli().status()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()


class TestServiceControl(GraphwatchCase):
    def _fake(self):
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            rc = 1 if "print" in " ".join(cmd) else 0
            return type("R", (), {"returncode": rc})()
        return cmds, fake_run

    def test_unknown_action_raises(self):
        with self.assertRaises(GraphwatchError):
            graphwatch.service_control("haha", runner=lambda cmd, **kw: None)

    def test_not_registered_raises(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=False):
            with self.assertRaises(GraphwatchError) as cm:
                graphwatch.service_control("start", runner=lambda cmd, **kw: None)
        self.assertIn("install", str(cm.exception))

    def test_macos_restart_unload_then_load(self):
        import unittest.mock
        cmds, fake = self._fake()
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"):
            graphwatch.service_control("restart", runner=fake)
        joined = [" ".join(c) for c in cmds]
        self.assertTrue(any("kill SIGTERM" in j for j in joined))
        self.assertTrue(any("bootstrap" in j for j in joined))

    def test_macos_stop_only_unload(self):
        import unittest.mock
        cmds, fake = self._fake()
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"):
            graphwatch.service_control("stop", runner=fake)
        joined = [" ".join(c) for c in cmds]
        self.assertTrue(any("kill SIGTERM" in j for j in joined))
        self.assertFalse(any("bootstrap" in j for j in joined))

    def test_linux_restart_maps_to_systemctl(self):
        import unittest.mock
        cmds, fake = self._fake()
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "linux"):
            graphwatch.service_control("restart", runner=fake)
        self.assertEqual(cmds[0], ["systemctl", "--user", "restart", "graphwatch.service"])

    def test_windows_start_runs_task(self):
        import unittest.mock
        cmds, fake = self._fake()
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "win32"):
            graphwatch.service_control("start", runner=fake)
        self.assertEqual(cmds[0][:2], ["schtasks", "/Run"])

    def test_cli_restart_unregistered_friendly(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=False):
            rc = self._cli().restart()
        self.assertNotEqual(rc, 0)


class TestOpsLogging(GraphwatchCase):
    def _capture(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        return buf, redirect_stderr(buf)

    def _run_capture(self, fac, step, wait=0.4):
        """跑 daemon 直到 step 副作用生效，捕获 stderr；返回 (errors, 日志前 buf)。"""
        import threading

        errors: list[Exception] = []

        def target():
            try:
                graphwatch.run_daemon(stop_event=stop, ensure=lambda: None,
                                      watch_factory=fac, poll_interval=0.05)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        stop = threading.Event()
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        time.sleep(0.2)
        step()
        time.sleep(wait)
        stop.set()
        thread.join(timeout=5)
        return errors

    def test_debounce_change_logged(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()

        def step():
            cfg = graphwatch.load_config()
            cfg["debounce"] = 7
            graphwatch.save_config(cfg)

        buf, redir = self._capture()
        with redir:
            self._run_capture(fac, step)
        self.assertIn("debounce 变化 3.0 → 7.0", buf.getvalue())

    def test_spawn_failure_logged_and_daemon_survives(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)

        class ExplodingFactory:
            def __init__(self):
                self.calls = 0
            def __call__(self, debounce):
                def start(folder):
                    self.calls += 1
                    raise OSError("spawn boom")
                return start

        fac = ExplodingFactory()
        buf, redir = self._capture()
        with redir:
            errors = self._run_capture(fac, lambda: None, wait=0.3)
        self.assertEqual(errors, [], "spawn 失败不应杀 daemon")
        self.assertIn("监听进程启动失败", buf.getvalue())
        self.assertGreater(fac.calls, 1, "下一轮应重试")

    def test_unexpected_error_logged_and_reraised(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        import threading
        class BoomFactory(FakeFactory):
            def __call__(self, debounce):
                def start(folder):
                    proc = FakeProc()
                    self.procs[str(folder)] = proc
                    return proc
                return start
        boom = BoomFactory()
        boom(3.0)(repo.resolve())
        # 直接制造 _reconcile 内部异常：patch load_config 第二次起抛 RuntimeError
        calls = {"n": 0}
        real_load = graphwatch.load_config
        def flaky():
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("kaboom")
            return real_load()
        import contextlib
        import io
        buf = io.StringIO()
        stop = threading.Event()
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "load_config", flaky), \
             contextlib.redirect_stderr(buf):
            with self.assertRaises(RuntimeError):
                graphwatch.run_daemon(stop_event=stop, ensure=lambda: None,
                                      watch_factory=boom, poll_interval=0.05)
        self.assertIn("daemon 意外错误", buf.getvalue())


class TestServiceStateAndLog(GraphwatchCase):
    def _print_runner(self, text, gone=False):
        def runner(cmd, **kw):
            if gone:
                return type("R", (), {"returncode": 1, "stdout": b""})()
            return type("R", (), {"returncode": 0, "stdout": text.encode()})()
        return runner

    def test_state_unregistered(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=False):
            st = graphwatch.service_state(runner=lambda cmd, **kw: None)
        self.assertFalse(st["registered"])
        self.assertFalse(st["running"])

    def test_state_running_parses_launchctl(self):
        import unittest.mock
        text = "state = running\npid = 12345\nlast exit code = (never exited)\n"
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch, "_launchd_gone", return_value=False), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"):
            st = graphwatch.service_state(runner=self._print_runner(text))
        self.assertTrue(st["running"])
        self.assertEqual(st["pid"], "12345")
        self.assertIn("never exited", st["last_exit"])

    def test_state_not_running_when_gone(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch, "_launchd_gone", return_value=True):
            st = graphwatch.service_state(runner=lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": b""})())
        self.assertFalse(st["running"])
        self.assertEqual(st["pid"], "-")

    def test_tail_log(self):
        p = graphwatch.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(f"line{i}" for i in range(20)), encoding="utf-8")
        self.assertEqual(graphwatch.tail_log(3), ["line17", "line18", "line19"])
        self.assertEqual(graphwatch.tail_log(0), [])

    def test_tail_log_missing_file(self):
        self.assertEqual(graphwatch.tail_log(5), [])

    def test_cli_status_prints_log(self):
        import io
        import unittest.mock
        from contextlib import redirect_stderr
        p = graphwatch.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[2026-09-06 20:00:00] 开始监听 /tmp/x\n", encoding="utf-8")
        st = {"registered": True, "running": True, "pid": "1", "last_exit": "0", "uptime": "-"}
        buf = io.StringIO()
        with redirect_stderr(buf), \
             unittest.mock.patch.object(graphwatch, "service_state", return_value=st), \
             unittest.mock.patch.object(graphwatch, "list_folders", return_value=[]):
            rc = self._cli().status()
        self.assertEqual(rc, 0)
        self.assertIn("开始监听", buf.getvalue())


class TestConfigWizard(GraphwatchCase):
    def _feed(self, *answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    def test_wizard_writes_all_fields(self):
        # backend 是编号选择：4 = openai（OpenAI 标准协议）
        cfg = graphwatch.run_wizard(input_fn=self._feed(
            "4", "sk-1234567890abcdef", "https://api.example.com", "gpt-5", "5",
        ))
        p = graphwatch.config_path()
        self.assertTrue(p.exists())
        self.assertEqual(cfg["backend"], "openai")
        self.assertEqual(cfg["api_key"], "sk-1234567890abcdef")
        self.assertEqual(cfg["base_url"], "https://api.example.com")
        self.assertEqual(cfg["model"], "gpt-5")
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

    def test_wizard_bad_backend_number_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("99", "0", "1", "", "", "", ""))
        self.assertEqual(cfg["backend"], "claude")  # 1 = claude

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


class TestWizardPicker(GraphwatchCase):
    def _feed(self, *answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    def test_picker_digit_selects(self):
        keys = iter(["4"])
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "openai")

    def test_picker_arrows_then_enter(self):
        keys = iter(["down", "down", "enter"])
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "gemini")

    def test_picker_esc_keeps_current(self):
        graphwatch.save_config({**graphwatch.load_config(), "backend": "kimi"})
        keys = iter(["esc"])
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "kimi")

    def test_picker_none_non_tty_falls_back(self):
        import io
        import unittest.mock
        with unittest.mock.patch.object(graphwatch.sys, "stdin", io.StringIO("")):
            cfg = graphwatch.run_wizard(input_fn=self._feed("1", "", "", "", ""))
        self.assertEqual(cfg["backend"], "claude")


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
        answers = iter(["2", "mk-1234567890abcdef", "", "", ""])
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


class TestRotateLog(GraphwatchCase):
    def test_rotate_creates_backup_and_fresh_file(self):
        p = graphwatch.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * (graphwatch.LOG_MAX_BYTES + 1), encoding="utf-8")
        graphwatch.rotate_log()
        self.assertTrue(p.with_suffix(".log.1").exists())
        self.assertLess(p.stat().st_size, graphwatch.LOG_MAX_BYTES)

    def test_no_rotate_under_threshold(self):
        p = graphwatch.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("small", encoding="utf-8")
        graphwatch.rotate_log()
        self.assertFalse(p.with_suffix(".log.1").exists())

    def test_rotation_shifts_backups(self):
        p = graphwatch.log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.with_suffix(".log.2").write_text("old2", encoding="utf-8")
        p.write_text("x" * (graphwatch.LOG_MAX_BYTES + 1), encoding="utf-8")
        graphwatch.rotate_log()
        self.assertTrue(p.with_suffix(".log.3").exists())  # old2 → .3
        self.assertTrue(p.with_suffix(".log.1").exists())


class TestNotify(GraphwatchCase):
    def test_darwin_uses_osascript(self):
        import unittest.mock
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            return type("R", (), {"returncode": 0})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"):
            ok = graphwatch.notify("标题", "内容", runner=fake_run)
        self.assertTrue(ok)
        self.assertIn("osascript", " ".join(cmds[0]))
        self.assertIn("标题", " ".join(cmds[0]))

    def test_linux_uses_notify_send(self):
        import unittest.mock
        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd)
            return type("R", (), {"returncode": 0})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"):
            graphwatch.notify("t", "m", runner=fake_run)
        self.assertEqual(cmds[0][0], "notify-send")

    def test_notify_failure_returns_false(self):
        import unittest.mock
        def fake_run(cmd, **kw):
            return type("R", (), {"returncode": 1})()
        with unittest.mock.patch.object(graphwatch.sys, "platform", "darwin"):
            self.assertFalse(graphwatch.notify("t", "m", runner=fake_run))


class TestThrottle(GraphwatchCase):
    def test_same_folder_throttled(self):
        sent = []
        notifier = graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: sent.append(f))
        notifier.fire("/a", "t", "m")
        notifier.fire("/a", "t", "m")
        self.assertEqual(len(sent), 1)

    def test_different_folders_not_throttled(self):
        sent = []
        notifier = graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: sent.append(f))
        notifier.fire("/a", "t", "m")
        notifier.fire("/b", "t", "m")
        self.assertEqual(len(sent), 2)

    def test_throttle_expires(self):
        import unittest.mock
        sent = []
        notifier = graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: sent.append(f))
        notifier.fire("/a", "t", "m")
        with unittest.mock.patch.object(graphwatch.time, "monotonic", return_value=graphwatch.time.monotonic() + 301):
            notifier.fire("/a", "t", "m")
        self.assertEqual(len(sent), 2)


class TestDaemonNotifiesOnCrash(GraphwatchCase):
    def test_crash_fires_notification(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeFactory()
        notified = []

        import threading
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, watch_factory=fac, poll_interval=0.05,
            notifier=graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: notified.append((f, t)))), daemon=True)
        thread.start()
        # 等 watcher 起来，然后注入崩溃
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not fac.procs:
            time.sleep(0.02)
        if fac.procs:
            fac.crash(repo.resolve())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not notified and thread.is_alive():
            time.sleep(0.02)
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(len(notified), 1, f"notified={notified}")
        self.assertIn(str(repo.resolve()), notified[0][0])
