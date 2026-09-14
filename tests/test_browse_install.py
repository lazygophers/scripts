"""native host manifest 注册的测试：全程用临时 HOME，一个字节都不碰真实用户目录。"""

from __future__ import annotations

import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib import browse_install as nh  # noqa: E402

BROWSE = pathlib.Path("/opt/lazygophers/bin/browse")


class FakeRegistry:
    """winreg 的替身：macOS 上跑不到真注册表，只断言键名和值。"""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.deleted: list[str] = []

    def set(self, key: str, value: str) -> None:
        self.keys[key] = value

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.keys.pop(key, None)


class TempHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def touch_dir(self, rel: str) -> pathlib.Path:
        path = self.home / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    def read(self, rel: str) -> dict:
        return json.loads((self.home / rel / f"{nh.HOST_NAME}.json").read_text("utf-8"))

    def wrapper(self, plat: str = "darwin") -> pathlib.Path:
        return nh.wrapper_path(self.home, plat)


class TestWrapper(TempHome):
    """manifest 没有 args 字段，参数只能靠 wrapper 带进去。"""

    def test_posix_content_and_exec_bit(self) -> None:
        path = nh.write_wrapper(self.home, "darwin", BROWSE)
        self.assertEqual(path, self.home / nh.WRAPPER_DIR / nh.WRAPPER_NAME)
        self.assertEqual(path.read_text("utf-8"),
                         '#!/bin/sh\nexec "/opt/lazygophers/bin/browse" --native-host "$@"\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

    def test_path_with_spaces_is_quoted(self) -> None:
        path = nh.write_wrapper(self.home, "linux", pathlib.Path("/opt/my tools/browse"))
        self.assertIn('exec "/opt/my tools/browse" --native-host "$@"',
                      path.read_text("utf-8"))

    def test_windows_is_a_cmd_with_crlf(self) -> None:
        path = nh.write_wrapper(self.home, "win32", pathlib.Path(r"C:\bin\browse.exe"))
        self.assertEqual(path.name, f"{nh.WRAPPER_NAME}.cmd")
        self.assertEqual(path.read_bytes(),
                         b'@echo off\r\n"C:\\bin\\browse.exe" --native-host %*\r\n')

    def test_rewrite_updates_target(self) -> None:
        nh.write_wrapper(self.home, "darwin", BROWSE)
        path = nh.write_wrapper(self.home, "darwin", pathlib.Path("/usr/local/bin/browse"))
        self.assertIn("/usr/local/bin/browse", path.read_text("utf-8"))
        self.assertNotIn(str(BROWSE), path.read_text("utf-8"))


class TestManifest(unittest.TestCase):
    def test_chromium_shape(self) -> None:
        m = nh.build_manifest(nh.CHROMIUM, BROWSE)
        self.assertEqual(m["name"], "com.lazygophers.browse")
        self.assertEqual(m["type"], "stdio")
        self.assertEqual(m["path"], str(BROWSE))
        self.assertEqual(m["allowed_origins"],
                         ["chrome-extension://podeceeeafjdcemppcgjhhokcokpcama/"])
        self.assertNotIn("allowed_extensions", m)

    def test_allowed_origins_rules(self) -> None:
        """末尾斜杠必带、不许有通配符、ID 恒为 32 位。"""
        m = nh.build_manifest(nh.CHROMIUM, BROWSE, ("aaaabbbbccccddddeeeeffffgggghhhh",
                                                    "iiiijjjjkkkkllllmmmmnnnnoooopppp"))
        self.assertEqual(len(m["allowed_origins"]), 2)
        for origin in m["allowed_origins"]:
            self.assertTrue(origin.startswith("chrome-extension://"))
            self.assertTrue(origin.endswith("/"))
            self.assertNotIn("*", origin)
            self.assertEqual(len(origin[len("chrome-extension://"):-1]), 32)

    def test_chromium_ids_deduped(self) -> None:
        m = nh.build_manifest(nh.CHROMIUM, BROWSE, ("dup", "dup", "other"))
        self.assertEqual(m["allowed_origins"],
                         ["chrome-extension://dup/", "chrome-extension://other/"])

    def test_gecko_uses_allowed_extensions(self) -> None:
        m = nh.build_manifest(nh.GECKO, BROWSE)
        self.assertEqual(m["allowed_extensions"], ["browse@lazygophers.com"])
        self.assertNotIn("allowed_origins", m)
        # gecko.id 是裸 ID，不是 URL
        for value in m["allowed_extensions"]:
            self.assertNotIn("://", value)

    def test_gecko_ids_deduped(self) -> None:
        m = nh.build_manifest(nh.GECKO, BROWSE, gecko_ids=("a@b", "a@b", "c@d"))
        self.assertEqual(m["allowed_extensions"], ["a@b", "c@d"])


class TestPlatformKey(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(nh.platform_key("darwin"), "darwin")
        self.assertEqual(nh.platform_key("win32"), "win32")
        self.assertEqual(nh.platform_key("windows"), "win32")
        self.assertEqual(nh.platform_key("linux"), "linux")
        self.assertEqual(nh.platform_key("freebsd13"), "linux")

    def test_default_is_current_platform(self) -> None:
        self.assertIn(nh.platform_key(), nh.BROWSERS)


class TestTable(unittest.TestCase):
    def test_every_platform_has_the_same_browsers(self) -> None:
        names = {plat: set(table) for plat, table in nh.BROWSERS.items()}
        self.assertEqual(len(set(map(frozenset, names.values()))), 1, names)

    def test_only_firefox_is_gecko(self) -> None:
        for plat, table in nh.BROWSERS.items():
            for name, (flavor, _, _) in table.items():
                expect = nh.GECKO if name == "firefox" else nh.CHROMIUM
                self.assertEqual(flavor, expect, f"{plat}/{name}")

    def test_windows_dests_are_all_registry(self) -> None:
        for name, (_, _, dests) in nh.BROWSERS["win32"].items():
            for dest in dests:
                self.assertTrue(dest.startswith("reg:"), f"{name}: {dest}")

    def test_posix_dests_are_all_dirs(self) -> None:
        for plat in ("darwin", "linux"):
            for name, (_, _, dests) in nh.BROWSERS[plat].items():
                for dest in dests:
                    self.assertFalse(dest.startswith("reg:"), f"{plat}/{name}: {dest}")

    def test_edge_manifest_carries_every_id(self) -> None:
        """Edge on Windows 只读第一个命中的 manifest，所以那一份必须授权所有 ID。"""
        ids = nh.EXTENSION_IDS + ("edgestoreidedgestoreidedgestorei",)
        m = nh.build_manifest(nh.CHROMIUM, BROWSE, ids)
        self.assertEqual(len(m["allowed_origins"]), len(ids))


class TestExtensionManifestAgreement(unittest.TestCase):
    """安装脚本里的 ID 必须和扩展 manifest 对得上，对不上就是授权失配。"""

    def setUp(self) -> None:
        path = REPO_ROOT / "browser-extension" / "extension" / "src" / "manifest.json"
        self.manifest = json.loads(path.read_text("utf-8"))

    def test_chromium_id_matches_the_pinned_key(self) -> None:
        """ID = 公钥 SHA-256 前 16 字节的 hex 逐位映射到 a-p。

        出处：`components/crx_file/id_util.cc:44-57`。key 写死了 ID 就固定，
        与扩展装在哪个目录无关。
        """
        import base64
        import hashlib

        digest = hashlib.sha256(base64.b64decode(self.manifest["key"])).digest()[:16]
        expect = "".join(chr(ord("a") + int(c, 16)) for c in digest.hex())
        self.assertEqual(nh.EXTENSION_IDS[0], expect)

    def test_gecko_id_matches(self) -> None:
        self.assertEqual(self.manifest["browser_specific_settings"]["gecko"]["id"],
                         nh.GECKO_IDS[0])


class TestDetect(TempHome):
    def test_nothing_installed(self) -> None:
        self.assertEqual(nh.detect(self.home, "darwin"), [])

    def test_detects_only_present(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        self.touch_dir("Library/Application Support/Firefox")
        self.assertEqual(sorted(nh.detect(self.home, "darwin")), ["chrome", "firefox"])

    def test_linux(self) -> None:
        self.touch_dir(".config/BraveSoftware/Brave-Browser")
        self.assertEqual(nh.detect(self.home, "linux"), ["brave"])

    def test_windows(self) -> None:
        self.touch_dir("AppData/Local/Microsoft/Edge/User Data")
        self.assertEqual(nh.detect(self.home, "win32"), ["edge"])


class TestInstallMac(TempHome):
    def test_chrome_path_and_permissions(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        done = nh.install(self.home, "darwin", BROWSE)
        self.assertEqual([n for n, _ in done], ["chrome"])
        path = self.home / nh._MAC_CHROME / f"{nh.HOST_NAME}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        # manifest 的 path 指 wrapper，不是 browse 本身，且必须是绝对路径
        written = json.loads(path.read_text("utf-8"))["path"]
        self.assertEqual(written, str(self.wrapper()))
        self.assertTrue(pathlib.Path(written).is_absolute())
        self.assertTrue(self.wrapper().stat().st_mode & stat.S_IXUSR)

    def test_brave_writes_both_dirs(self) -> None:
        """brave-core 指 Chrome 目录、KeePassXC 指 BraveSoftware 目录，两个都写。"""
        self.touch_dir("Library/Application Support/BraveSoftware/Brave-Browser")
        nh.install(self.home, "darwin", BROWSE)
        brave = self.read("Library/Application Support/BraveSoftware/"
                          "Brave-Browser/NativeMessagingHosts")
        chrome = self.read(nh._MAC_CHROME)
        self.assertEqual(brave, chrome)
        self.assertIn("allowed_origins", brave)

    def test_opera_lands_in_chrome_dir(self) -> None:
        self.touch_dir("Library/Application Support/com.operasoftware.Opera")
        done = nh.install(self.home, "darwin", BROWSE)
        self.assertEqual(done, [("opera", str(self.home / nh._MAC_CHROME /
                                              f"{nh.HOST_NAME}.json"))])

    def test_firefox_dir_and_field(self) -> None:
        self.touch_dir("Library/Application Support/Firefox")
        nh.install(self.home, "darwin", BROWSE)
        m = self.read("Library/Application Support/Mozilla/NativeMessagingHosts")
        self.assertEqual(m["allowed_extensions"], list(nh.GECKO_IDS))

    def test_undetected_browser_gets_nothing(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        nh.install(self.home, "darwin", BROWSE)
        self.assertFalse((self.home / "Library/Application Support/Vivaldi").exists())
        self.assertFalse((self.home / "Library/Application Support/Mozilla").exists())

    def test_explicit_browsers_skip_detection(self) -> None:
        done = nh.install(self.home, "darwin", BROWSE, browsers=["vivaldi"])
        self.assertEqual([n for n, _ in done], ["vivaldi"])
        self.assertTrue(self.read("Library/Application Support/Vivaldi/"
                                  "NativeMessagingHosts"))

    def test_extra_ids_reach_the_file(self) -> None:
        done = nh.install(self.home, "darwin", BROWSE, browsers=["chrome"],
                          extension_ids=nh.EXTENSION_IDS + ("zzz",))
        self.assertEqual(len(done), 1)
        self.assertIn("chrome-extension://zzz/", self.read(nh._MAC_CHROME)["allowed_origins"])

    def test_rerun_is_idempotent(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        first = nh.install(self.home, "darwin", BROWSE)
        second = nh.install(self.home, "darwin", BROWSE)
        self.assertEqual(first, second)
        path = self.home / nh._MAC_CHROME / f"{nh.HOST_NAME}.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)


class TestInstallLinux(TempHome):
    def test_every_browser_lands_where_spec_says(self) -> None:
        expect = {
            "chrome": ".config/google-chrome/NativeMessagingHosts",
            "chromium": ".config/chromium/NativeMessagingHosts",
            "edge": ".config/microsoft-edge/NativeMessagingHosts",
            "brave": ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
            "opera": ".config/google-chrome/NativeMessagingHosts",
            "vivaldi": ".config/vivaldi/NativeMessagingHosts",
            "firefox": ".mozilla/native-messaging-hosts",
        }
        for name, rel in expect.items():
            with self.subTest(name):
                nh.install(self.home, "linux", BROWSE, browsers=[name])
                self.assertTrue((self.home / rel / f"{nh.HOST_NAME}.json").is_file())


class TestInstallWindows(TempHome):
    def test_registry_value_points_at_the_file(self) -> None:
        reg = FakeRegistry()
        self.touch_dir("AppData/Local/Google/Chrome/User Data")
        nh.install(self.home, "win32", BROWSE, reg_set=reg.set)
        key = r"SOFTWARE\Google\Chrome\NativeMessagingHosts\com.lazygophers.browse"
        self.assertIn(key, reg.keys)
        path = pathlib.Path(reg.keys[key])
        self.assertEqual(path, self.home / nh.WIN_MANIFEST_DIR / f"{nh.HOST_NAME}.json")
        self.assertEqual(json.loads(path.read_text("utf-8"))["type"], "stdio")

    def test_chromium_falls_back_to_chrome_key(self) -> None:
        reg = FakeRegistry()
        nh.install(self.home, "win32", BROWSE, browsers=["chromium"], reg_set=reg.set)
        self.assertEqual(sorted(reg.keys), [
            r"SOFTWARE\Chromium\NativeMessagingHosts\com.lazygophers.browse",
            r"SOFTWARE\Google\Chrome\NativeMessagingHosts\com.lazygophers.browse",
        ])

    def test_firefox_uses_its_own_key_and_file(self) -> None:
        reg = FakeRegistry()
        nh.install(self.home, "win32", BROWSE, browsers=["firefox"], reg_set=reg.set)
        key = r"SOFTWARE\Mozilla\NativeMessagingHosts\com.lazygophers.browse"
        path = pathlib.Path(reg.keys[key])
        self.assertTrue(path.name.endswith(".firefox.json"))
        self.assertIn("allowed_extensions", json.loads(path.read_text("utf-8")))

    def test_chromium_and_gecko_files_do_not_collide(self) -> None:
        reg = FakeRegistry()
        nh.install(self.home, "win32", BROWSE, browsers=["chrome", "firefox"],
                   reg_set=reg.set)
        files = sorted(p.name for p in (self.home / nh.WIN_MANIFEST_DIR).iterdir())
        self.assertEqual(files, [f"{nh.WRAPPER_NAME}.cmd",
                                 "com.lazygophers.browse.firefox.json",
                                 "com.lazygophers.browse.json"])

    def test_manifest_points_at_the_cmd_wrapper(self) -> None:
        reg = FakeRegistry()
        nh.install(self.home, "win32", BROWSE, browsers=["chrome"], reg_set=reg.set)
        manifest = json.loads(nh.win_manifest_path(self.home, nh.CHROMIUM)
                              .read_text("utf-8"))
        self.assertEqual(manifest["path"], str(self.wrapper("win32")))
        self.assertTrue(manifest["path"].endswith(".cmd"))


class TestUninstall(TempHome):
    def test_removes_every_file(self) -> None:
        names = list(nh.BROWSERS["darwin"])
        nh.install(self.home, "darwin", BROWSE, browsers=names)
        left_before = list(self.home.rglob(f"{nh.HOST_NAME}*.json"))
        self.assertTrue(left_before)
        self.assertTrue(self.wrapper().exists())
        nh.uninstall(self.home, "darwin")
        self.assertEqual(list(self.home.rglob(f"{nh.HOST_NAME}*.json")), [])
        self.assertFalse(self.wrapper().exists())
        self.assertFalse((self.home / nh.WRAPPER_DIR).exists())

    def test_reports_what_it_removed(self) -> None:
        nh.install(self.home, "darwin", BROWSE, browsers=["vivaldi"])
        removed = nh.uninstall(self.home, "darwin")
        self.assertEqual([n for n, _ in removed], ["vivaldi", "wrapper"])

    def test_is_safe_when_nothing_installed(self) -> None:
        self.assertEqual(nh.uninstall(self.home, "darwin"), [])

    def test_windows_deletes_keys_and_files(self) -> None:
        reg = FakeRegistry()
        names = list(nh.BROWSERS["win32"])
        nh.install(self.home, "win32", BROWSE, browsers=names, reg_set=reg.set)
        nh.uninstall(self.home, "win32", reg_delete=reg.delete)
        self.assertEqual(reg.keys, {})
        self.assertIn(r"SOFTWARE\Mozilla\NativeMessagingHosts\com.lazygophers.browse",
                      reg.deleted)
        self.assertFalse((self.home / nh.WIN_MANIFEST_DIR).exists())

    def test_leaves_other_hosts_alone(self) -> None:
        nh.install(self.home, "darwin", BROWSE, browsers=["chrome"])
        other = self.home / nh._MAC_CHROME / "com.someone.else.json"
        other.write_text("{}", encoding="utf-8")
        nh.uninstall(self.home, "darwin")
        self.assertTrue(other.is_file())


class TestResolveBrowsePath(TempHome):
    def test_explicit_path(self) -> None:
        target = self.home / "browse"
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        self.assertEqual(nh.resolve_browse_path(str(target)), target.resolve())

    def test_explicit_missing_path_raises(self) -> None:
        with self.assertRaises(ValueError):
            nh.resolve_browse_path(str(self.home / "nope"))

    def test_path_lookup_wins(self) -> None:
        """PATH 上装好的 browse 优先：uvx 那种临时环境里的路径活不过这次运行。"""
        import unittest.mock as mock

        found = self.home / "browse"
        found.write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.object(nh.shutil, "which", lambda _: str(found)):
            self.assertEqual(nh.resolve_browse_path(None), found.resolve())

    def test_falls_back_to_the_repo_bin(self) -> None:
        import unittest.mock as mock

        with mock.patch.object(nh.shutil, "which", lambda _: None):
            self.assertEqual(nh.resolve_browse_path(None),
                             (REPO_ROOT / "bin" / "browse").resolve())

    def test_raises_when_browse_is_nowhere(self) -> None:
        import unittest.mock as mock

        with mock.patch.object(nh, "REPO_ROOT", self.home / "nowhere"), \
                mock.patch.object(nh.shutil, "which", lambda _: None), \
                self.assertRaises(ValueError):
            nh.resolve_browse_path(None)


class FakeWinreg:
    """够 _reg_set / _reg_delete 用的最小 winreg：真机上跑不到，形状必须对。"""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def CreateKey(self, root, key):  # noqa: N802 — 照抄 winreg 的名字
        import contextlib

        assert root == self.HKEY_CURRENT_USER
        self.values.setdefault(key, "")
        return contextlib.nullcontext(key)

    def SetValueEx(self, handle, name, reserved, typ, value):  # noqa: N802
        assert name == "" and typ == self.REG_SZ
        self.values[handle] = value

    def DeleteKey(self, root, key):  # noqa: N802
        assert root == self.HKEY_CURRENT_USER
        if key not in self.values:
            raise FileNotFoundError(key)
        del self.values[key]


class TestRegistryBackend(unittest.TestCase):
    """需要: 实机验证。这里只证明键名/值/删除语义，真 winreg 在 macOS 上不存在。"""

    def setUp(self) -> None:
        import unittest.mock as mock

        self.winreg = FakeWinreg()
        patcher = mock.patch.dict(sys.modules, {"winreg": self.winreg})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_set_then_delete(self) -> None:
        key = r"SOFTWARE\Google\Chrome\NativeMessagingHosts\com.lazygophers.browse"
        nh._reg_set(key, r"C:\manifest.json")
        self.assertEqual(self.winreg.values[key], r"C:\manifest.json")
        nh._reg_delete(key)
        self.assertNotIn(key, self.winreg.values)

    def test_delete_missing_key_is_quiet(self) -> None:
        nh._reg_delete(r"SOFTWARE\Nope\com.lazygophers.browse")


class TestCli(TempHome):
    def run_cli(self, *args: str) -> int:
        import io
        import unittest.mock as mock

        # 固定成 darwin：CLI 的行为与跑测试的机器是什么系统无关。
        # stderr 换成 StringIO 有两个作用：`isatty()` 恒为 False，交互那半边
        # （构建扩展、等 120 秒、**写系统策略要管理员密码**）在测试里一步都不会跑；
        # 顺带把 reporter 的输出收走，不再喷到跑测试的人的终端上。
        with mock.patch.object(nh.pathlib.Path, "home", staticmethod(lambda: self.home)), \
                mock.patch.object(nh, "platform_key", lambda *a: "darwin"), \
                mock.patch("sys.stderr", new=io.StringIO()):
            return nh.main(["browse install", *args])

    def test_install_and_uninstall_round_trip(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        self.assertEqual(self.run_cli("--browsers", "chrome"), nh.EXIT_OK)
        path = self.home / nh._MAC_CHROME / f"{nh.HOST_NAME}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(self.run_cli("--uninstall"), nh.EXIT_OK)
        self.assertFalse(path.exists())

    def test_list_writes_nothing(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        self.assertEqual(self.run_cli("--list"), nh.EXIT_OK)
        self.assertEqual(list(self.home.rglob(f"{nh.HOST_NAME}*.json")), [])

    def test_unknown_browser_is_usage_error(self) -> None:
        self.assertEqual(self.run_cli("--browsers", "netscape"), nh.EXIT_USAGE)

    def test_nothing_detected_fails(self) -> None:
        self.assertEqual(self.run_cli(), nh.EXIT_FAILED)

    def test_bad_browse_path_is_usage_error(self) -> None:
        self.assertEqual(self.run_cli("--browse-path", str(self.home / "nope")),
                         nh.EXIT_USAGE)

    def test_uninstall_on_clean_home(self) -> None:
        self.assertEqual(self.run_cli("--uninstall"), nh.EXIT_OK)

    def test_extension_id_flag_reaches_the_manifest(self) -> None:
        self.assertEqual(self.run_cli("--browsers", "chrome", "--extension-id", "zzz"),
                         nh.EXIT_OK)
        self.assertIn("chrome-extension://zzz/",
                      self.read(nh._MAC_CHROME)["allowed_origins"])

    def test_gecko_id_flag_reaches_the_manifest(self) -> None:
        self.assertEqual(self.run_cli("--browsers", "firefox", "--gecko-id", "x@y"),
                         nh.EXIT_OK)
        m = self.read("Library/Application Support/Mozilla/NativeMessagingHosts")
        self.assertIn("x@y", m["allowed_extensions"])


class TestManualToggles(TempHome):
    """装完之后那三个只能人点的开关，必须出现在输出里。

    程序侧没有任何入口（要写浏览器企业策略，而那等于改浏览器自己的配置；无痕那条连策略
    字段都没有）。不说的话用户只会以为「装完就该全都能用」，然后对着不工作的 file://
    去查别的地方。
    """

    def run_interactive(self, *args: str) -> str:
        import io
        import unittest.mock as mock

        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        self.touch_dir("Library/Application Support/Google/Chrome")
        with mock.patch.object(nh.pathlib.Path, "home", staticmethod(lambda: self.home)), \
                mock.patch.object(nh, "platform_key", lambda *a: "darwin"), \
                mock.patch.object(nh, "copy_to_clipboard", lambda text: False), \
                mock.patch("sys.stderr", new=Tty()) as err:
            nh.main(["browse install", "--browsers", "chrome",
                     "--no-build", "--no-wait", *args])
        return err.getvalue()

    def test_all_three_toggles_are_listed(self) -> None:
        out = self.run_interactive()
        for word in ("固定到工具栏", "允许访问文件网址", "在无痕模式下启用"):
            self.assertIn(word, out)

    def test_they_are_marked_optional_not_required(self) -> None:
        """写成必做步骤会让人以为不点就用不了 —— 三条都是按需。"""
        self.assertIn("按需", self.run_interactive())

    def test_it_says_nobody_can_click_them_for_you(self) -> None:
        self.assertIn("只能你自己点", self.run_interactive())


if __name__ == "__main__":
    unittest.main()


class TestGeckoIdStaysInSync(unittest.TestCase):
    """扩展 manifest 的 gecko.id 必须与安装脚本的 GECKO_IDS 一致。

    两边对不上时 Firefox 会拒绝 native messaging 的授权，而且报错发生在
    用户机器上、本地测不出来。这条测试把「两处要同步」变成机器强制。
    """

    def test_manifest_gecko_id_matches_installer(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "browser-extension" / "extension" / "src" / "manifest.json").read_text()
        )
        gecko_id = manifest["browser_specific_settings"]["gecko"]["id"]
        self.assertIn(gecko_id, nh.GECKO_IDS)


class TestInteractiveFlow(unittest.TestCase):
    """`browse install` 的交互流程：构建、指引、等待验证。

    这些只在终端里跑（`sys.stderr.isatty()`），脚本和 CI 里 `install` 只做注册 ——
    否则管道里跑会被一个 120 秒的等待卡死。
    """

    def test_build_is_skipped_when_dist_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp)
            (src / "dist").mkdir()
            (src / "dist" / "manifest.json").write_text("{}")
            # package.json 不存在：真跑构建会抛 FileNotFoundError，没抛就证明它早退了
            self.assertEqual(nh.build_extension(src), src / "dist")

    def test_build_refuses_when_there_is_no_extension_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                nh.build_extension(pathlib.Path(tmp))

    def test_waiting_stops_early_when_the_error_is_not_about_the_browser(self) -> None:
        """退出码 3 才是「还没连上」，其余退出码说明是别的毛病，不该干等满 120 秒。"""
        calls: list[int] = []

        def fake_run(cmd, **kwargs):
            calls.append(1)
            return subprocess.CompletedProcess(cmd, returncode=2)

        with unittest.mock.patch.object(nh.subprocess, "run", fake_run):
            self.assertFalse(nh.wait_for_extension(pathlib.Path("/x/browse"), timeout=99))
        self.assertEqual(len(calls), 1, "退出码 2 应当立刻返回，不重试")

    def test_waiting_succeeds_as_soon_as_a_command_goes_through(self) -> None:
        results = iter([3, 3, 0])

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=next(results))

        with unittest.mock.patch.object(nh.subprocess, "run", fake_run), \
                unittest.mock.patch.object(nh.time, "sleep", lambda _: None):
            self.assertTrue(nh.wait_for_extension(pathlib.Path("/x/browse"), timeout=99))
