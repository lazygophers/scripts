"""bridge 的系统服务：文件长什么样、装/卸各跑了哪些命令。

真装一个服务到机器上是不可接受的测试副作用，所以 `runner` 是注入的：这里只断言
「写出的内容对不对」和「打算跑的命令对不对」，执行本身交给系统。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_service


class FakeRunner:
    """记下每条命令；`fail` 里的命令返回非 0，用来测「启用失败怎么办」。"""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, command, capture_output=False):
        self.calls.append(list(command))
        code = 1 if command[0] in self.fail else 0
        return subprocess.CompletedProcess(command, code)


class UnitTextCase(unittest.TestCase):
    def test_macos_plist_runs_the_bridge_and_never_idles_out(self) -> None:
        text = browse_service.unit_text("darwin", "/usr/local/bin/browse", "/run/b.sock")
        self.assertIn("<string>/usr/local/bin/browse</string>", text)
        self.assertIn("<string>bridge</string>", text)
        # 常驻服务必须关掉空闲自退，否则 launchd 只会一遍遍把它拉起来
        self.assertIn("<string>--idle-timeout</string>", text)
        self.assertIn("<string>0</string>", text)
        self.assertIn("<key>RunAtLoad</key><true/>", text)
        self.assertIn("<key>KeepAlive</key><true/>", text)
        # 日志统一走 lib/log.py 的单一 JSONL，plist 不再重定向 stdout/stderr
        self.assertNotIn("StandardOutPath", text)
        self.assertNotIn("StandardErrorPath", text)

    def test_systemd_unit_restarts_and_never_idles_out(self) -> None:
        text = browse_service.unit_text("linux", "/usr/bin/browse", "/run/b.sock")
        self.assertIn("ExecStart=/usr/bin/browse bridge run --socket /run/b.sock --idle-timeout 0",
                      text)
        self.assertIn("Restart=always", text)
        self.assertIn("WantedBy=default.target", text)

    def test_windows_startup_script_is_a_cmd_file(self) -> None:
        text = browse_service.unit_text("win32", r"C:\\bin\\browse.exe", r"C:\\b.sock")
        self.assertIn("--idle-timeout 0", text)
        self.assertTrue(text.startswith("@echo off"))

    def test_each_platform_has_its_own_target_path(self) -> None:
        home = pathlib.Path("/home/u")
        self.assertEqual(browse_service.unit_path(home, "darwin"),
                         home / "Library/LaunchAgents/com.lazygophers.browse.bridge.plist")
        self.assertEqual(browse_service.unit_path(home, "linux"),
                         home / ".config/systemd/user/browse-bridge.service")
        self.assertIn("Startup", str(browse_service.unit_path(home, "win32")))


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)

    def install(self, plat: str, runner=None):
        return browse_service.install(self.home, plat, "/usr/bin/browse", "/run/b.sock",
                                      runner=runner or FakeRunner())

    def test_linux_writes_the_unit_then_enables_it(self) -> None:
        runner = FakeRunner()
        path, results = self.install("linux", runner)
        self.assertTrue(path.is_file())
        self.assertEqual(runner.calls, [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "browse-bridge.service"],
        ])
        self.assertTrue(all(code == 0 for _, code in results))

    def test_macos_boots_out_first_so_a_reinstall_works(self) -> None:
        runner = FakeRunner()
        _, _ = self.install("darwin", runner)
        self.assertEqual(runner.calls[0][:2], ["launchctl", "bootout"])
        self.assertEqual(runner.calls[1][:2], ["launchctl", "bootstrap"])

    def test_windows_just_drops_the_file(self) -> None:
        runner = FakeRunner()
        path, results = self.install("win32", runner)
        self.assertTrue(path.is_file())
        self.assertEqual(runner.calls, [])
        self.assertEqual(results, [])

    def test_a_missing_service_manager_is_reported_not_raised(self) -> None:
        def explode(command, capture_output=False):
            raise FileNotFoundError(command[0])
        _, results = self.install("linux", explode)
        self.assertEqual([code for _, code in results], [127, 127])

    def test_reinstall_overwrites_instead_of_stacking(self) -> None:
        path, _ = self.install("linux")
        path.write_text("stale", encoding="utf-8")
        self.install("linux")
        self.assertIn("ExecStart=", path.read_text(encoding="utf-8"))


class UninstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)

    def test_uninstall_disables_then_deletes(self) -> None:
        browse_service.install(self.home, "linux", "/usr/bin/browse", "/run/b.sock",
                               runner=FakeRunner())
        runner = FakeRunner()
        path, results = browse_service.uninstall(self.home, "linux", runner=runner)
        self.assertIsNotNone(path)
        self.assertFalse(browse_service.unit_path(self.home, "linux").exists())
        self.assertEqual(runner.calls[0][:4], ["systemctl", "--user", "disable", "--now"])
        self.assertTrue(all(code == 0 for _, code in results))

    def test_uninstalling_something_never_installed_is_not_an_error(self) -> None:
        runner = FakeRunner()
        path, results = browse_service.uninstall(self.home, "linux", runner=runner)
        self.assertIsNone(path)
        self.assertEqual(results, [])
        self.assertEqual(runner.calls, [])

    def test_status_reports_the_file_and_the_path(self) -> None:
        self.assertFalse(browse_service.status(self.home, "linux")["installed"])
        browse_service.install(self.home, "linux", "/usr/bin/browse", "/run/b.sock",
                               runner=FakeRunner())
        state = browse_service.status(self.home, "linux")
        self.assertTrue(state["installed"])
        self.assertTrue(state["path"].endswith("browse-bridge.service"))


if __name__ == "__main__":
    unittest.main()


class VolatileSocketCase(unittest.TestCase):
    """socket 落在临时目录：必须拒装（2026-09-26 事故护栏），一个字节都不写盘。

    那次事故：测试环境的临时 HOME 真跑了 `launchctl bootstrap`，装出一个 socket 指向
    /tmp 的常驻任务，占着 WS 端口把正常 daemon 的自动拉起全部挤死。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)

    def test_tmp_socket_is_refused_without_writing(self):
        runner = FakeRunner()
        with self.assertRaises(ValueError):
            browse_service.install(self.home, "darwin", "/usr/bin/browse",
                                   "/tmp/x/.local/state/lazygophers/scripts/browse.sock",
                                   runner=runner)
        self.assertFalse(browse_service.unit_path(self.home, "darwin").exists())
        self.assertEqual(runner.calls, [])

    def test_var_folders_socket_is_refused_too(self):
        # macOS 的 $TMPDIR（/var/folders/...）同样是重启即清空的位置
        with self.assertRaises(ValueError):
            browse_service.install(self.home, "linux", "/usr/bin/browse",
                                   "/var/folders/aa/bb/T/browse.sock",
                                   runner=FakeRunner())

    def test_real_home_socket_still_installs(self):
        runner = FakeRunner()
        path, _ = browse_service.install(self.home, "linux", "/usr/bin/browse",
                                         "/home/u/.local/state/lazygophers/scripts/browse.sock",
                                         runner=runner)
        self.assertTrue(path.is_file())
        self.assertTrue(runner.calls)
