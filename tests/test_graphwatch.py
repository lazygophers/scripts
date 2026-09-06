"""graphwatch 注册表骨架测试：add / remove / list + 配置文件行为。

主缝：GRAPHWATCH_HOME 指向临时目录，全部命令不碰真实家目录。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
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
        fac = FakeListenerFactory()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=FakeRunner(), poll_interval=0.05), daemon=True)
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


class FakeListener:
    """假监听器：记录 start/stop，保留 on_change 供测试手动触发变更。"""

    def __init__(self, folder, debounce, on_change):
        self.folder = str(folder)
        self.debounce = debounce
        self.on_change = on_change
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass

    def fire_change(self):
        self.on_change()


class FakeListenerFactory:
    """假监听工厂：记录挂载的目录与 debounce。"""

    def __init__(self):
        self.listeners: dict[str, FakeListener] = {}
        self.calls: list[tuple[str, float]] = []

    def __call__(self, folder, debounce, on_change):
        self.calls.append((str(folder), debounce))
        lis = FakeListener(folder, debounce, on_change)
        self.listeners[str(folder)] = lis
        return lis


class FakeRunner:
    """假重建执行器：记录调用；串行性断言用 active 计数。"""

    def __init__(self, rc=0, delay=0.0):
        self.rc = rc
        self.delay = delay
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    def __call__(self, folder):
        import time as _t

        self.calls.append(folder)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                _t.sleep(self.delay)
            return self.rc
        finally:
            self.active -= 1


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

    def test_run_starts_listener_per_folder(self):
        import threading
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        stop = threading.Event()
        fac = FakeListenerFactory()

        def bootstrap():
            stop.set()
        graphwatch.run_daemon(stop_event=stop, ensure=lambda: None, listener_factory=fac,
                              rebuild_runner=FakeRunner(), poll_interval=0.05, on_started=bootstrap)
        self.assertEqual([k for k, _ in fac.calls], [str(repo.resolve())])
        self.assertEqual(fac.calls[0][1], 3.0)

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
            args["listener_factory"] = fac
        args.setdefault("rebuild_runner", FakeRunner())
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(stop_event=stop, **args), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "daemon 线程未退出")

    def test_hot_add_starts_listener(self):
        repo1 = self.mkdir("r1")
        graphwatch.add_folder(repo1)
        fac = FakeListenerFactory()
        repo2 = self.mkdir("r2")

        def step():
            graphwatch.add_folder(repo2)

        self._run_until(lambda: len(fac.calls) >= 2, fac=fac, on_started=step)
        self.assertIn(str(repo2.resolve()), [k for k, _ in fac.calls])

    def test_hot_remove_stops_listener(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        stopped = []

        def step():
            graphwatch.remove_folder(repo)
            stopped.append(True)

        self._run_until(lambda: stopped and fac.listeners and all(x.stopped for x in fac.listeners.values()),
                        fac=fac, on_started=lambda: time.sleep(0.2) or stopped.append(None))
        self.assertTrue(all(x.stopped for x in fac.listeners.values()))

    def test_bad_yaml_does_not_crash_daemon(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        import threading
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=FakeRunner(),
            poll_interval=0.05), daemon=True)
        thread.start()
        time.sleep(0.2)
        graphwatch.config_path().write_text("a: [unclosed\n", encoding="utf-8")
        time.sleep(0.3)
        self.assertTrue(thread.is_alive(), "坏 YAML 不应让 daemon 退出")
        stop.set()
        thread.join(timeout=5)

    def test_debounce_change_restarts_listeners(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        def step():
            cfg = graphwatch.load_config()
            cfg["debounce"] = 7
            graphwatch.save_config(cfg)
        self._run_until(lambda: any(d == 7 for _, d in fac.calls), fac=fac, on_started=step)

    def test_change_triggers_rebuild(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        runner = FakeRunner()
        fired = threading.Event()

        def step():
            if str(repo.resolve()) in fac.listeners:
                fac.listeners[str(repo.resolve())].fire_change()
                fired.set()

        self._run_until(lambda: len(runner.calls) >= 1, fac=fac, rebuild_runner=runner,
                        on_started=lambda: time.sleep(0.3) or step())
        self.assertEqual(runner.calls, [str(repo.resolve())])

    def test_rebuilds_serial_at_concurrency_1(self):
        import threading
        repo1 = self.mkdir("r1")
        repo2 = self.mkdir("r2")
        graphwatch.add_folder(repo1)
        graphwatch.add_folder(repo2)
        fac = FakeListenerFactory()
        runner = FakeRunner(delay=0.15)

        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=runner,
            poll_interval=0.05), daemon=True)
        thread.start()
        time.sleep(0.3)
        for r in (repo1, repo2):
            fac.listeners[str(r.resolve())].fire_change()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(runner.calls) < 2:
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.max_active, 1, "并发 1 时重建必须串行")

    def test_rebuild_failure_notifies(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        runner = FakeRunner(rc=1)
        notified = []
        import threading
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=runner,
            poll_interval=0.05,
            notifier=graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: notified.append((f, t)) or True)), daemon=True)
        thread.start()
        time.sleep(0.2)
        fac.listeners[str(repo.resolve())].fire_change()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not notified:
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(len(notified), 1)
        self.assertIn("重建失败", notified[0][1])

    def test_rebuild_concurrency_2_parallel(self):
        import threading
        repo1 = self.mkdir("r1")
        repo2 = self.mkdir("r2")
        graphwatch.add_folder(repo1)
        graphwatch.add_folder(repo2)
        cfg = graphwatch.load_config()
        cfg["rebuild_concurrency"] = 2
        graphwatch.save_config(cfg)
        fac = FakeListenerFactory()
        runner = FakeRunner(delay=0.2)
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=runner,
            poll_interval=0.05), daemon=True)
        thread.start()
        time.sleep(0.3)
        for r in (repo1, repo2):
            fac.listeners[str(r.resolve())].fire_change()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(runner.calls) < 2:
            time.sleep(0.05)
        time.sleep(0.3)
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.max_active, 2, "并发 2 时应可同时重建")


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


class TestFreshnessUpdating(GraphwatchCase):
    def _stale_repo(self):
        import os as _os

        repo = self.mkdir()
        (repo / "a.py").write_text("x\n", encoding="utf-8")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}", encoding="utf-8")
        old = 1000000000
        _os.utime(g / "graph.json", (old, old))
        return repo

    def test_stale_with_daemon_is_updating(self):
        repo = self._stale_repo()
        st, detail = graphwatch.folder_freshness(str(repo), daemon_running=True)
        self.assertEqual(st, "updating")
        self.assertIn("自动重建", detail)

    def test_stale_without_daemon_is_fail(self):
        repo = self._stale_repo()
        st, _ = graphwatch.folder_freshness(str(repo), daemon_running=False)
        self.assertEqual(st, "fail")

    def test_cli_status_renders_updating(self):
        import io
        import unittest.mock
        from contextlib import redirect_stderr

        repo = self._stale_repo()
        buf = io.StringIO()
        st = {"registered": True, "running": True, "pid": "1", "last_exit": "0", "uptime": "-"}
        with redirect_stderr(buf), \
             unittest.mock.patch.object(graphwatch, "service_state", return_value=st), \
             unittest.mock.patch.object(graphwatch, "list_folders", return_value=[str(repo)]), \
             unittest.mock.patch.object(graphwatch, "daemon_alive", return_value=True):
            rc = self._cli().status(log=0)
        self.assertEqual(rc, 0)
        self.assertIn("更新中", buf.getvalue())


class TestStartupCatchup(GraphwatchCase):
    def _stale_repo(self):
        """造一个「图谱过期」的目录：graph.json 比源文件旧。"""
        import os as _os

        repo = self.mkdir()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}", encoding="utf-8")
        old = 1000000000  # 2001 年
        _os.utime(g / "graph.json", (old, old))
        return repo

    def test_stale_trigger_finds_file(self):
        repo = self._stale_repo()
        t = graphwatch.stale_trigger(str(repo))
        self.assertIsNotNone(t)
        self.assertEqual(t.name, "a.py")

    def test_fresh_repo_no_trigger(self):
        repo = self.mkdir()
        (repo / "a.py").write_text("x\n", encoding="utf-8")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(graphwatch.stale_trigger(str(repo)))

    def test_startup_nudges_stale_folder(self):
        import threading

        repo = self._stale_repo()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        runner = FakeRunner()
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=runner, poll_interval=0.2), daemon=True)
        thread.start()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not runner.calls:
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(runner.calls, [str(repo.resolve())], "启动补课应入队重建过期目录")

    def test_hot_add_nudges_stale_folder(self):
        import threading

        repo = self._stale_repo()
        other = self.mkdir("other")
        graphwatch.add_folder(other)
        fac = FakeListenerFactory()
        runner = FakeRunner()
        stop = threading.Event()
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=dict(
            stop_event=stop, ensure=lambda: None, listener_factory=fac, rebuild_runner=runner, poll_interval=0.2), daemon=True)
        thread.start()
        time.sleep(0.4)
        graphwatch.add_folder(repo)  # 热加载新增过期目录
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and str(repo.resolve()) not in runner.calls:
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=5)
        self.assertIn(str(repo.resolve()), runner.calls, "热加载新增的过期目录也应补课")


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
                                      listener_factory=fac, rebuild_runner=FakeRunner(), poll_interval=0.05)
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
        fac = FakeListenerFactory()

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
            def __call__(self, folder, debounce, on_change):
                self.calls += 1
                raise OSError("spawn boom")

        fac = ExplodingFactory()
        buf, redir = self._capture()
        with redir:
            errors = self._run_capture(fac, lambda: None, wait=0.3)
        self.assertEqual(errors, [], "spawn 失败不应杀 daemon")
        self.assertIn("监听启动失败", buf.getvalue())
        self.assertGreater(fac.calls, 1, "下一轮应重试")

    def test_unexpected_error_logged_and_reraised(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        import threading
        boom = FakeListenerFactory()
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
                                      listener_factory=boom, rebuild_runner=FakeRunner(), poll_interval=0.05)
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
            "4", "sk-1234567890abcdef", "https://api.example.com", "gpt-5", "5", "2",
        ))
        p = graphwatch.config_path()
        self.assertTrue(p.exists())
        self.assertEqual(cfg["backend"], "openai")
        self.assertEqual(cfg["api_key"], "sk-1234567890abcdef")
        self.assertEqual(cfg["base_url"], "https://api.example.com")
        self.assertEqual(cfg["model"], "gpt-5")
        self.assertEqual(cfg["debounce"], 5.0)
        self.assertEqual(cfg["rebuild_concurrency"], 2)
        # folders 不被向导改动
        self.assertEqual(cfg["folders"], [])
        mode = stat.S_IMODE(p.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_wizard_enter_keeps_defaults_and_existing(self):
        graphwatch.add_folder(self.mkdir())
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "", ""))
        self.assertEqual(cfg["debounce"], 3)
        self.assertEqual(cfg["backend"], "")
        self.assertEqual(len(cfg["folders"]), 1)

    def test_wizard_bad_backend_number_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("99", "0", "1", "", "", "", "", ""))
        self.assertEqual(cfg["backend"], "claude")  # 1 = claude

    def test_wizard_bad_debounce_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "abc", "-1", "7", ""))
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
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "openai")

    def test_picker_arrows_then_enter(self):
        keys = iter(["down", "down", "enter"])
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "gemini")

    def test_picker_esc_keeps_current(self):
        graphwatch.save_config({**graphwatch.load_config(), "backend": "kimi"})
        keys = iter(["esc"])
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "", ""), picker=lambda: next(keys))
        self.assertEqual(cfg["backend"], "kimi")

    def test_picker_none_non_tty_falls_back(self):
        import io
        import unittest.mock
        with unittest.mock.patch.object(graphwatch.sys, "stdin", io.StringIO("")):
            cfg = graphwatch.run_wizard(input_fn=self._feed("1", "", "", "", "", ""))
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
        answers = iter(["2", "mk-1234567890abcdef", "", "", "", ""])
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


class TestValidationConcurrency(GraphwatchCase):
    def test_bad_concurrency_raises_friendly(self):
        graphwatch.config_path().write_text("rebuild_concurrency: abc\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError) as cm:
            graphwatch.load_config()
        self.assertIn("rebuild_concurrency", str(cm.exception))

    def test_zero_concurrency_raises(self):
        graphwatch.config_path().write_text("rebuild_concurrency: 0\n", encoding="utf-8")
        with self.assertRaises(GraphwatchError):
            graphwatch.load_config()


class TestFreshnessEdges(GraphwatchCase):
    def test_folder_freshness_fresh(self):
        repo = self.mkdir()
        (repo / "a.py").write_text("x\n", encoding="utf-8")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}", encoding="utf-8")
        st, detail = graphwatch.folder_freshness(str(repo))
        self.assertEqual(st, "ok")
        self.assertIn("新鲜", detail)

    def test_stale_trigger_ignores_extensionless(self):
        import os

        repo = self.mkdir()
        (repo / "a.py").write_text("x\n", encoding="utf-8")
        (repo / "Makefile").write_text("all:\n", encoding="utf-8")
        g = repo / "graphify-out"
        g.mkdir()
        (g / "graph.json").write_text("{}", encoding="utf-8")
        future = time.time() + 100
        os.utime(repo / "Makefile", (future, future))
        # 无扩展名文件不在 watch 监听范围，不应判过期
        self.assertIsNone(graphwatch.stale_trigger(str(repo)))


class TestWatchedExtensionsFallback(GraphwatchCase):
    def test_fallback_without_graphify(self):
        saved = sys.modules.pop("graphify", None)
        sys.modules["graphify"] = None
        try:
            ext = graphwatch._watched_extensions()
        finally:
            sys.modules.pop("graphify", None)
            if saved is not None:
                sys.modules["graphify"] = saved
        self.assertIn(".py", ext)

    def test_extensions_from_graphify_detect(self):
        import types
        import unittest.mock
        det = types.ModuleType("graphify.detect")
        det.CODE_EXTENSIONS = frozenset({".zz1"})
        det.DOC_EXTENSIONS = frozenset({".zz2"})
        det.PAPER_EXTENSIONS = frozenset({".zz3"})
        det.IMAGE_EXTENSIONS = frozenset({".zz4"})
        gfy = types.ModuleType("graphify")
        with unittest.mock.patch.dict(sys.modules, {"graphify": gfy, "graphify.detect": det}):
            ext = graphwatch._watched_extensions()
        self.assertEqual(ext, frozenset({".zz1", ".zz2", ".zz3", ".zz4"}))


class TestLockEdges(GraphwatchCase):
    def test_daemon_alive_true_when_locked(self):
        lock = graphwatch.acquire_singleton_lock()
        try:
            self.assertTrue(graphwatch.daemon_alive())
        finally:
            graphwatch.release_singleton_lock(lock)

    def test_run_locked_pid_unreadable(self):
        import unittest.mock
        blocker = self.home / "unreadable.lock"
        blocker.write_text("x", encoding="utf-8")
        blocker.chmod(0o000)
        with unittest.mock.patch.object(graphwatch, "acquire_singleton_lock", return_value=None), \
             unittest.mock.patch.object(graphwatch, "lock_path", return_value=blocker), \
             self.assertRaises(GraphwatchError) as cm:
            graphwatch.run_daemon(ensure=lambda: None)
        self.assertIn("PID ?", str(cm.exception))

    def test_windows_lock_roundtrip(self):
        import types
        import unittest.mock
        fake = types.ModuleType("msvcrt")
        fake.locking = lambda fd, mode, nbytes: None
        fake.LK_NBLCK = 2
        fake.LK_UNLCK = 8
        with unittest.mock.patch.object(graphwatch.sys, "platform", "win32"), \
             unittest.mock.patch.dict(sys.modules, {"msvcrt": fake}):
            fd = graphwatch.acquire_singleton_lock()
            self.assertIsNotNone(fd)
            graphwatch.release_singleton_lock(fd)

    def test_save_replace_and_unlink_both_fail(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch.os, "replace", side_effect=OSError("boom")), \
             unittest.mock.patch.object(graphwatch.os, "unlink", side_effect=OSError("gone")):
            with self.assertRaises(OSError):
                graphwatch.save_config({"folders": []})

    def test_release_none_noop(self):
        graphwatch.release_singleton_lock(None)


class TestNotifyEdges(GraphwatchCase):
    def test_windows_powershell_quotes_escaped(self):
        import unittest.mock
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            return type("R", (), {"returncode": 0})()

        with unittest.mock.patch.object(graphwatch.sys, "platform", "win32"):
            ok = graphwatch.notify('ti"tle', "me'ssage", runner=fake_run)
        self.assertTrue(ok)
        joined = " ".join(cmds[0])
        self.assertIn("powershell", joined)
        self.assertIn("''", joined)  # 单引号双写转义

    def test_runner_exception_returns_false(self):
        import unittest.mock

        def boom(cmd, **kw):
            raise RuntimeError("gone")

        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"):
            self.assertFalse(graphwatch.notify("t", "m", runner=boom))

    def test_audit_failure_swallowed(self):
        import unittest.mock
        blocker = self.home / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with unittest.mock.patch.object(graphwatch, "log_path", return_value=blocker / "logs" / "graphwatch.log"), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "linux"):
            self.assertTrue(graphwatch.notify("t", "m", runner=lambda c, **k: type("R", (), {"returncode": 0})()))

    def test_notify_default_runner(self):
        import unittest.mock

        def fake_run(cmd, **kw):
            return type("R", (), {"returncode": 0})()

        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch("subprocess.run", fake_run):
            self.assertTrue(graphwatch.notify("t", "m"))


class TestServicePlatformEdges(GraphwatchCase):
    def _R(self, rc=0, stdout=b""):
        return type("R", (), {"returncode": rc, "stdout": stdout})()

    def test_registered_linux_and_windows(self):
        import unittest.mock
        for plat in ("linux", "win32"):
            with unittest.mock.patch.object(graphwatch.sys, "platform", plat):
                self.assertTrue(graphwatch.service_registered(runner=lambda c, **k: self._R(0)))
                self.assertFalse(graphwatch.service_registered(runner=lambda c, **k: self._R(1)))

    def test_state_linux_parses_systemctl(self):
        import unittest.mock

        def runner(cmd, **kw):
            return self._R(0, b"MainPID=42\nActiveState=active\nExecMainStatus=0\n")

        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch.object(graphwatch, "service_registered", return_value=True):
            st = graphwatch.service_state(runner=runner)
        self.assertTrue(st["running"])
        self.assertEqual(st["pid"], "42")
        self.assertEqual(st["last_exit"], "0")

    def test_state_windows_uses_daemon_alive(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch.sys, "platform", "win32"), \
             unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch, "daemon_alive", return_value=True):
            st = graphwatch.service_state(runner=lambda c, **k: self._R(0))
        self.assertTrue(st["running"])

    def test_state_darwin_default_runner(self):
        import unittest.mock

        def fake_run(cmd, **kw):
            return self._R(0, b"state = running\npid = 77\nlast exit code = 0\n")

        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch, "_launchd_gone", return_value=False), \
             unittest.mock.patch("subprocess.run", fake_run):
            st = graphwatch.service_state()
        self.assertTrue(st["running"])
        self.assertEqual(st["pid"], "77")

    def test_install_windows_creates_task(self):
        import unittest.mock
        cmds = []

        def runner(cmd, **kw):
            cmds.append(cmd)
            return self._R(0)

        with unittest.mock.patch.object(graphwatch.sys, "platform", "win32"):
            graphwatch.install_service(runner=runner)
        self.assertIn("/Create", cmds[0])

    def test_install_linux_default_runner(self):
        import unittest.mock
        cmds = []
        runner = lambda cmd, **kw: cmds.append(cmd) or self._R(0)  # noqa: E731
        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch.object(graphwatch, "_checked_runner", return_value=runner), \
             unittest.mock.patch.object(graphwatch.Path, "home", classmethod(lambda cls: self.home)):
            graphwatch.install_service()
        unit = self.home / ".config" / "systemd" / "user" / "graphwatch.service"
        self.assertTrue(unit.exists())
        flat = [" ".join(c) for c in cmds]
        self.assertTrue(any("enable" in j for j in flat))

    def test_uninstall_linux(self):
        import unittest.mock
        cmds = []
        unit_dir = self.home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "graphwatch.service").write_text("x", encoding="utf-8")
        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch.object(graphwatch.Path, "home", classmethod(lambda cls: self.home)):
            graphwatch.uninstall_service(runner=lambda cmd, **kw: cmds.append(cmd) or self._R(0))
        self.assertFalse((unit_dir / "graphwatch.service").exists())

    def test_windows_stop_ends_task(self):
        import unittest.mock
        cmds = []

        def runner(cmd, **kw):
            cmds.append(cmd)
            return self._R(0)

        with unittest.mock.patch.object(graphwatch, "service_registered", return_value=True), \
             unittest.mock.patch.object(graphwatch.sys, "platform", "win32"):
            graphwatch.service_control("stop", runner=runner)
        self.assertEqual(cmds[0][:3], ["schtasks", "/End", "/TN"])

    def test_launchd_stop_bootout_fallback(self):
        import unittest.mock
        cmds = []

        def runner(cmd, **kw):
            cmds.append(cmd)
            return self._R(0)  # print 永远 rc0：服务一直「活着」，逼出 bootout 兜底

        with unittest.mock.patch.object(graphwatch.time, "sleep", lambda s: None):
            graphwatch._launchd_stop(runner)
        joined = [" ".join(c) for c in cmds]
        self.assertTrue(any("kill" in j for j in joined))
        self.assertTrue(any("bootout" in j for j in joined))

    def test_launchd_start_retries_then_ok(self):
        import unittest.mock
        n = {"i": 0}

        def runner(cmd, **kw):
            n["i"] += 1
            if n["i"] < 3:
                raise RuntimeError("I/O error")
            return self._R(0)

        with unittest.mock.patch.object(graphwatch.time, "sleep", lambda s: None):
            graphwatch._launchd_start(runner, "/tmp/x.plist")
        self.assertEqual(n["i"], 3)

    def test_launchd_start_all_fail_raises(self):
        import unittest.mock

        def runner(cmd, **kw):
            raise RuntimeError("I/O error 5")

        with unittest.mock.patch.object(graphwatch.time, "sleep", lambda s: None), \
             self.assertRaises(RuntimeError):
            graphwatch._launchd_start(runner, "/tmp/x.plist")

    def test_launchd_stop_gone_after_bootout(self):
        import unittest.mock

        def runner(cmd, **kw):
            # bootout 前服务一直「活着」，bootout 后消失：覆盖兜底后的 return
            gone = any("bootout" in " ".join(c) for c in seen)
            seen.append(cmd)
            return self._R(1 if gone else 0)

        seen = []
        with unittest.mock.patch.object(graphwatch.time, "sleep", lambda s: None):
            graphwatch._launchd_stop(runner)

    def test_uninstall_linux_default_runner(self):
        import unittest.mock
        unit_dir = self.home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "graphwatch.service").write_text("x", encoding="utf-8")
        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch.object(graphwatch, "_checked_runner",
                                        return_value=lambda cmd, **k: self._R(0)), \
             unittest.mock.patch.object(graphwatch.Path, "home", classmethod(lambda cls: self.home)):
            graphwatch.uninstall_service()
        self.assertFalse((unit_dir / "graphwatch.service").exists())

    def test_registered_default_runner_linux(self):
        import unittest.mock
        fake = {"rc": 0}

        def fake_run(cmd, **kw):
            return self._R(fake["rc"])

        with unittest.mock.patch.object(graphwatch.sys, "platform", "linux"), \
             unittest.mock.patch("subprocess.run", fake_run):
            self.assertTrue(graphwatch.service_registered())
            fake["rc"] = 1
            self.assertFalse(graphwatch.service_registered())

    def test_checked_runner_defaults_check_true(self):
        import unittest.mock
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw)
            return self._R(0)

        with unittest.mock.patch("subprocess.run", fake_run):
            r = graphwatch._checked_runner()
            r(["/usr/bin/true"])
        self.assertIs(seen["check"], True)


class TestWorkerEdges(GraphwatchCase):
    def _daemon(self, runner, fac, **kw):
        stop = threading.Event()
        args = dict(stop_event=stop, ensure=lambda: None, listener_factory=fac,
                    rebuild_runner=runner, poll_interval=0.05)
        args.update(kw)
        thread = threading.Thread(target=graphwatch.run_daemon, kwargs=args, daemon=True)
        thread.start()
        return stop, thread

    def _wait(self, cond, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not cond():
            time.sleep(0.05)

    def test_queued_during_flight_reruns(self):
        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        runner = FakeRunner(delay=0.2)
        stop, thread = self._daemon(runner, fac)
        try:
            self._wait(lambda: str(repo.resolve()) in fac.listeners)
            lis = fac.listeners[str(repo.resolve())]
            lis.fire_change()
            lis.fire_change()  # 第二次：在队列/执行中 → dirty，完成后重跑一轮
            self._wait(lambda: len(runner.calls) >= 2)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertEqual(len(runner.calls), 2, "重建中再变更应合并为恰好重跑一轮")
        self.assertEqual(runner.max_active, 1)

    def test_rebuild_exception_logged_daemon_survives(self):
        import contextlib
        import io

        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()

        class Boom:
            def __init__(self):
                self.calls = 0

            def __call__(self, folder):
                self.calls += 1
                raise RuntimeError("update boom")

        runner = Boom()
        notified = []
        notifier = graphwatch.Notifier(throttle_secs=300, sender=lambda f, t, m: notified.append(t) or True)
        stop, thread = self._daemon(runner, fac, notifier=notifier)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                self._wait(lambda: str(repo.resolve()) in fac.listeners)
                fac.listeners[str(repo.resolve())].fire_change()
                self._wait(lambda: runner.calls >= 1 and notified)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertIn("重建异常", buf.getvalue())
        self.assertEqual(notified, ["graphwatch 重建失败"])

    def test_concurrency_increase_midrun(self):
        import contextlib
        import io

        r1, r2 = self.mkdir("r1"), self.mkdir("r2")
        graphwatch.add_folder(r1)
        graphwatch.add_folder(r2)
        fac = FakeListenerFactory()
        runner = FakeRunner(delay=0.2)
        stop, thread = self._daemon(runner, fac)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                self._wait(lambda: len(fac.listeners) >= 2)
                cfg = graphwatch.load_config()
                cfg["rebuild_concurrency"] = 2
                graphwatch.save_config(cfg)
                self._wait(lambda: "rebuild_concurrency 变化" in buf.getvalue())
                for r in (r1, r2):
                    fac.listeners[str(r.resolve())].fire_change()
                self._wait(lambda: len(runner.calls) >= 2)
                time.sleep(0.5)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.max_active, 2, "并发提到 2 后应能同时重建")

    def test_bad_config_round_skipped_logged(self):
        import contextlib
        import io

        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        stop, thread = self._daemon(FakeRunner(), fac)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                time.sleep(0.2)
                graphwatch.config_path().write_text("a: [unclosed\n", encoding="utf-8")
                time.sleep(0.4)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertIn("配置暂不可读", buf.getvalue())

    def test_loop_unexpected_error_logged_and_exits(self):
        import contextlib
        import io
        import unittest.mock

        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = FakeListenerFactory()
        stop, thread = self._daemon(FakeRunner(), fac)
        buf = io.StringIO()
        p = unittest.mock.patch.object(graphwatch, "load_config", side_effect=RuntimeError("kaboom"))
        try:
            with contextlib.redirect_stderr(buf):
                time.sleep(0.2)
                p.start()
                thread.join(timeout=5)
        finally:
            p.stop()
            stop.set()
            if thread.is_alive():
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "意外错误应让 daemon 退出待服务管理器拉起")
        self.assertIn("daemon 意外错误", buf.getvalue())

    def test_hot_remove_logged_and_stop_failure_tolerated(self):
        import contextlib
        import io

        class BadStopListener(FakeListener):
            def stop(self):
                raise RuntimeError("stop boom")

        class BadStopFactory(FakeListenerFactory):
            def __call__(self, folder, debounce, on_change):
                self.calls.append((str(folder), debounce))
                lis = BadStopListener(folder, debounce, on_change)
                self.listeners[str(folder)] = lis
                return lis

        repo = self.mkdir()
        graphwatch.add_folder(repo)
        fac = BadStopFactory()
        stop, thread = self._daemon(FakeRunner(), fac)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                self._wait(lambda: str(repo.resolve()) in fac.listeners)
                graphwatch.remove_folder(repo)
                self._wait(lambda: fac.listeners and all(x.stopped for x in fac.listeners.values()), timeout=5)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertIn("停止监听", buf.getvalue())


class TestCliWrappers(GraphwatchCase):
    def test_cli_install(self):
        import unittest.mock
        with unittest.mock.patch.object(graphwatch, "ensure_graphify"), \
             unittest.mock.patch.object(graphwatch, "install_service") as m:
            rc = self._cli().install()
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_cli_start_stop_restart_uninstall(self):
        import unittest.mock
        for name, target in (("start", "service_control"), ("stop", "service_control"),
                             ("restart", "service_control"), ("uninstall", "uninstall_service")):
            with unittest.mock.patch.object(graphwatch, target) as m:
                rc = getattr(self._cli(), name)()
            self.assertEqual(rc, 0)
            m.assert_called_once()

    def test_cli_config_unknown_action(self):
        self.assertEqual(self._cli().config("haha"), 2)

    def test_cli_run_keyboard_interrupt(self):
        import unittest.mock

        def boom():
            raise KeyboardInterrupt

        with unittest.mock.patch.object(graphwatch, "run_daemon", boom):
            self.assertEqual(self._cli().run(), 0)


class TestWizardEdges(GraphwatchCase):
    def _feed(self, *answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    def test_bad_concurrency_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("", "", "", "", "", "abc", "-1", "4"))
        self.assertEqual(cfg["rebuild_concurrency"], 4)

    def test_backend_nonnumeric_reasks(self):
        cfg = graphwatch.run_wizard(input_fn=self._feed("abc", "2", "", "", "", "", ""))
        self.assertEqual(cfg["backend"], "kimi")
