"""browse_install 补测：构建、剪贴板、等待连上、服务安装、main 的各条分支。

不跑 npm、不装服务、不碰用户 HOME：subprocess 和 browse_service 全部 patch，
Windows 注册表用假的 `winreg` 模块塞进 sys.modules（本机是 macOS，真模块不存在）。
"""

import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_install as bi  # noqa: E402


def fake_winreg(**attrs) -> types.ModuleType:
    """最小 winreg：只给出本模块用到的那几个名字。"""
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = "HKCU"
    module.DeleteKey = mock.MagicMock()
    module.OpenKey = mock.MagicMock()
    module.QueryValueEx = mock.MagicMock(return_value=("C:\\wrapper.cmd", 1))
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class TestRegistryHelpers(unittest.TestCase):
    """_reg_delete / _reg_query：键不在都当作「没注册」，不抛给上层。"""

    def test_delete_missing_key_is_ignored(self):
        winreg = fake_winreg(DeleteKey=mock.MagicMock(side_effect=FileNotFoundError))
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            self.assertIsNone(bi._reg_delete("Software\\x"))

    def test_delete_calls_winreg(self):
        winreg = fake_winreg()
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            bi._reg_delete("Software\\x")
        winreg.DeleteKey.assert_called_once_with("HKCU", "Software\\x")

    def test_query_returns_default_value(self):
        winreg = fake_winreg()
        winreg.OpenKey.return_value.__enter__.return_value = "handle"
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            self.assertEqual(bi._reg_query("Software\\x"), "C:\\wrapper.cmd")

    def test_query_missing_key_is_none(self):
        winreg = fake_winreg(OpenKey=mock.MagicMock(side_effect=OSError))
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            self.assertIsNone(bi._reg_query("Software\\x"))


class TestBuildExtension(unittest.TestCase):
    """build_extension：源码不在直接报错；node_modules 缺了才先 npm install。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = pathlib.Path(self.tmp.name) / "browse"
        (self.src / "src").mkdir(parents=True)
        (self.src.parent / "shared").mkdir()

    def test_missing_package_json_raises(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            bi.build_extension(self.src)
        self.assertIn("--no-build", str(ctx.exception))

    def test_installs_deps_then_builds(self):
        (self.src / "package.json").write_text("{}")
        (self.src.parent / "shared" / "package.json").write_text("{}")
        with mock.patch("subprocess.run") as run:
            dist = bi.build_extension(self.src)
        self.assertEqual(dist, self.src / "dist")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [["npm", "install"], ["npm", "install"], ["npm", "run", "build"]])

    def test_existing_node_modules_skips_install(self):
        (self.src / "package.json").write_text("{}")
        (self.src / "node_modules").mkdir()
        (self.src.parent / "shared" / "package.json").write_text("{}")
        (self.src.parent / "shared" / "node_modules").mkdir()
        with mock.patch("subprocess.run") as run:
            bi.build_extension(self.src)
        self.assertEqual([call.args[0] for call in run.call_args_list], [["npm", "run", "build"]])


class TestCopyToClipboard(unittest.TestCase):
    """copy_to_clipboard：按平台挑工具，失败一律 False（不算装失败）。"""

    def test_darwin_uses_pbcopy(self):
        with mock.patch.object(bi, "platform_key", return_value="darwin"), \
             mock.patch("subprocess.run") as run:
            self.assertIs(bi.copy_to_clipboard("/tmp/dist"), True)
        self.assertEqual(run.call_args.args[0], ["pbcopy"])
        self.assertEqual(run.call_args.kwargs["input"], b"/tmp/dist")

    def test_unknown_platform_returns_false(self):
        with mock.patch.object(bi, "platform_key", return_value="freebsd"):
            self.assertIs(bi.copy_to_clipboard("/tmp/dist"), False)

    def test_tool_failure_returns_false(self):
        with mock.patch.object(bi, "platform_key", return_value="linux"), \
             mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertIs(bi.copy_to_clipboard("/tmp/dist"), False)

    def test_nonzero_exit_returns_false(self):
        with mock.patch.object(bi, "platform_key", return_value="darwin"), \
             mock.patch("subprocess.run",
                        side_effect=subprocess.CalledProcessError(1, ["pbcopy"])):
            self.assertIs(bi.copy_to_clipboard("/tmp/dist"), False)


class TestWaitForExtension(unittest.TestCase):
    """wait_for_extension：0 通了、3 继续等、其余退出码立刻收手。"""

    def _run(self, codes, timeout=0.05):
        results = [mock.MagicMock(returncode=c) for c in codes]
        with mock.patch("lib.lazyhelp._resolve", return_value="/usr/local/bin/browse"), \
             mock.patch("subprocess.run", side_effect=results) as run, \
             mock.patch.object(bi, "CONNECT_POLL_SECONDS", 0):
            got = bi.wait_for_extension(timeout)
        return got, run

    def test_success_returns_true(self):
        got, run = self._run([0])
        self.assertIs(got, True)
        self.assertEqual(run.call_args.args[0][1:],
                         ["browsingContext", "getTree", "--no-say"])

    def test_other_exit_code_stops_waiting(self):
        got, run = self._run([2])
        self.assertIs(got, False)
        self.assertEqual(run.call_count, 1)  # 不是「没连上」，不再干等

    def test_not_connected_then_connected(self):
        got, run = self._run([3, 0], timeout=5)
        self.assertIs(got, True)
        self.assertEqual(run.call_count, 2)

    def test_timeout_returns_false(self):
        got, _ = self._run([3], timeout=-1)
        self.assertIs(got, False)


class TestInstallService(unittest.TestCase):
    """install_service：装不上不算 install 失败，只是返回 False。"""

    def test_missing_browse_binary_skips(self):
        out = mock.MagicMock()
        with mock.patch("lib.lazyhelp._resolve", return_value=None):
            self.assertIs(bi.install_service(out, pathlib.Path("/home/u"), "darwin"), False)
        self.assertIn("找不到 browse 可执行文件", out.info.call_args.args[0])

    def test_enable_failure_is_reported_but_not_fatal(self):
        out = mock.MagicMock()
        with mock.patch("lib.lazyhelp._resolve", return_value="/bin/browse"), \
             mock.patch.object(bi.browse_service, "install",
                               return_value=("/tmp/plist", [(["launchctl", "load"], 1)])):
            self.assertIs(bi.install_service(out, pathlib.Path("/home/u"), "darwin"), False)
        self.assertIn("启用没成功", out.info.call_args_list[0].args[0])

    def test_bootout_failure_is_not_counted(self):
        # `launchctl bootout` 在没装过时本来就非零，不算失败
        out = mock.MagicMock()
        with mock.patch("lib.lazyhelp._resolve", return_value="/bin/browse"), \
             mock.patch.object(bi.browse_service, "install",
                               return_value=("/tmp/plist", [(["launchctl", "bootout", "x"], 1)])):
            self.assertIs(bi.install_service(out, pathlib.Path("/home/u"), "darwin"), True)
        self.assertIn("开机自启", out.ok.call_args.args[0])


class MainCase(unittest.TestCase):
    """基类：main 的所有外部动作都换成 mock。"""

    def setUp(self):
        self.out = mock.MagicMock()
        for p in (mock.patch.object(bi, "reporter", return_value=self.out),
                  mock.patch.object(bi, "install_service", return_value=True),
                  mock.patch.object(bi, "copy_to_clipboard", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def infos(self) -> str:
        return "\n".join(str(c.args[0]) for c in self.out.info.call_args_list)

    def errs(self) -> str:
        return "\n".join(str(c.args[0]) for c in self.out.err.call_args_list)


class TestMainUninstall(MainCase):
    """--uninstall：清注册 + 卸服务，都是幂等的。"""

    def test_removes_registrations_and_service(self):
        with mock.patch.object(bi, "uninstall", return_value=[("chrome", "/x/manifest.json")]), \
             mock.patch.object(bi.browse_service, "uninstall", return_value=("/tmp/plist", [])):
            rc = bi.main(["browse install", "--uninstall"])
        self.assertEqual(rc, bi.EXIT_OK)
        self.assertIn("chrome: 已删 /x/manifest.json", self.out.ok.call_args_list[0].args[0])

    def test_nothing_registered_says_so(self):
        with mock.patch.object(bi, "uninstall", return_value=[]), \
             mock.patch.object(bi.browse_service, "uninstall", return_value=(None, [])):
            rc = bi.main(["browse install", "--uninstall"])
        self.assertEqual(rc, bi.EXIT_OK)
        self.assertIn("没有找到任何旧注册", self.infos())
        self.assertIn("bridge 没装成服务", self.infos())


class TestMainInstall(MainCase):
    """install：构建 → 指引加载 → 等连上。"""

    def test_build_failure_exits_failed(self):
        with mock.patch.object(bi, "build_extension",
                               side_effect=subprocess.CalledProcessError(1, ["npm"])):
            rc = bi.main(["browse install", "--no-wait"])
        self.assertEqual(rc, bi.EXIT_FAILED)
        self.assertIn("构建扩展失败", self.out.err.call_args.args[0])
        self.assertIn("手动构建", self.infos())

    def test_no_build_skips_npm(self):
        with mock.patch.object(bi, "build_extension") as build:
            rc = bi.main(["browse install", "--no-build", "--no-wait"])
        self.assertEqual(rc, bi.EXIT_OK)
        build.assert_not_called()
        self.assertIn("装完自己验", self.infos())

    def test_clipboard_hint_only_when_copied(self):
        with mock.patch.object(bi, "copy_to_clipboard", return_value=True), \
             mock.patch.object(bi, "build_extension", return_value=pathlib.Path("/x/dist")):
            bi.main(["browse install", "--no-wait"])
        self.assertIn("路径已复制到剪贴板", self.infos())

    def test_no_service_flag_skips_service(self):
        with mock.patch.object(bi, "install_service") as service, \
             mock.patch.object(bi, "build_extension", return_value=pathlib.Path("/x/dist")):
            bi.main(["browse install", "--no-build", "--no-wait", "--no-service"])
        service.assert_not_called()

    def _wait_main(self, **patches):
        stderr = mock.MagicMock()
        stderr.isatty.return_value = True
        with mock.patch.object(bi, "build_extension", return_value=pathlib.Path("/x/dist")), \
             mock.patch("sys.stderr", stderr), \
             mock.patch.object(bi, "wait_for_extension", **patches):
            return bi.main(["browse install"])

    def test_connected_reports_success(self):
        self.assertEqual(self._wait_main(return_value=True), bi.EXIT_OK)
        self.assertIn("整条链路通了", self.out.ok.call_args.args[0])

    def test_timeout_prints_troubleshooting(self):
        self.assertEqual(self._wait_main(return_value=False), bi.EXIT_FAILED)
        self.assertIn("超时：扩展还没连上", self.errs())
        self.assertIn("chrome://extensions", self.errs())

    def test_ctrl_c_is_not_a_failure(self):
        self.assertEqual(self._wait_main(side_effect=KeyboardInterrupt), bi.EXIT_OK)
        self.assertIn("没等到", self.infos())

    def test_non_tty_does_not_wait(self):
        stderr = mock.MagicMock()
        stderr.isatty.return_value = False
        with mock.patch.object(bi, "build_extension", return_value=pathlib.Path("/x/dist")), \
             mock.patch("sys.stderr", stderr), \
             mock.patch.object(bi, "wait_for_extension") as wait:
            rc = bi.main(["browse install"])
        self.assertEqual(rc, bi.EXIT_OK)
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
