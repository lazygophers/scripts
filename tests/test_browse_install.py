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

    def wrapper(self, plat: str = "darwin", browser: str = "chrome") -> pathlib.Path:
        return nh.wrapper_path(self.home, plat, browser)

    def seed(self, plat: str = "darwin", browsers=(), browse=None, reg_set=None):
        """写一份旧式 native messaging 注册（manifest + wrapper）。

        install() 已随连接层重做删除；uninstall/status 测的是「清残留/查残留」，
        布景自己搭 —— 内容和 2026-09-15 之前 install() 写出来的一致。
        """
        browse = browse or (self.touch_dir("fake-browse") / "browse")
        if not browse.exists():
            browse.write_text("#!/bin/sh\n", encoding="utf-8")
        for name in browsers:
            flavor, _, dests = nh.BROWSERS[plat][name]
            wrapper = nh.wrapper_path(self.home, plat, name)
            wrapper.parent.mkdir(parents=True, exist_ok=True)
            wrapper.write_text(
                f'#!/bin/sh\nexec "{browse}" --native-host --browser {name} "$@"\n',
                encoding="utf-8")
            wrapper.chmod(0o755)
            manifest = {"name": nh.HOST_NAME, "description": nh.DESCRIPTION,
                        "path": str(wrapper), "type": "stdio",
                        "allowed_origins": [f"chrome-extension://{i}/" for i in nh.EXTENSION_IDS]}
            if flavor == nh.GECKO:
                manifest.pop("allowed_origins")
                manifest["allowed_extensions"] = list(nh.GECKO_IDS)
            for dest in dests:
                if dest.startswith("reg:"):
                    path = nh.win_manifest_path(self.home, flavor)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                    reg_set(f"{dest[4:]}\\{nh.HOST_NAME}", str(path))
                else:
                    path = self.home / dest / f"{nh.HOST_NAME}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(manifest), encoding="utf-8")
        return browse




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

    def test_no_two_browsers_share_a_dest(self) -> None:
        """共享落点 = 两个浏览器写同一个文件互相覆盖，后写的 wrapper 赢 ——
        2026-09-15 实锤：没装 opera 的机器上 Chrome 的连接被标成 opera。"""
        for plat, table in nh.BROWSERS.items():
            seen: dict[str, str] = {}
            for name, (_, _, dests) in table.items():
                for dest in dests:
                    self.assertNotIn(dest, seen,
                                     f"{plat}: {seen.get(dest)} 和 {name} 共用 {dest}")
                    seen[dest] = name

    def test_posix_dests_are_all_dirs(self) -> None:
        for plat in ("darwin", "linux"):
            for name, (_, _, dests) in nh.BROWSERS[plat].items():
                for dest in dests:
                    self.assertFalse(dest.startswith("reg:"), f"{plat}/{name}: {dest}")

    def test_origin_allowlist_covers_every_id(self) -> None:
        """bridge 的 WS Origin 白名单 = EXTENSION_IDS，一个都不能少（多 ID 分发时）。"""
        ids = nh.EXTENSION_IDS + ("edgestoreidedgestoreidedgestorei",)
        origins = {f"chrome-extension://{i}/" for i in ids}
        self.assertEqual(len(origins), len(ids), "ID 重复即授权失配")


class TestExtensionManifestAgreement(unittest.TestCase):
    """安装脚本里的 ID 必须和扩展 manifest 对得上，对不上就是授权失配。"""

    def setUp(self) -> None:
        path = REPO_ROOT / "browser-extension" / "browse" / "src" / "manifest.json"
        self.manifest = json.loads(path.read_text("utf-8"))

    def test_chromium_id_matches_the_pinned_key(self) -> None:
        """ID = 公钥 SHA-256 前 16 字节的 hex 逐位映射到 a-p。

        出处：`components/crx_file/id_util.cc:44-57`。key 写死了 ID 就固定，
        与扩展装在哪个目录无关。
        """
        import base64
        import hashlib

        key = self.manifest["key"]
        padded = key + "=" * (-len(key) % 4)  # manifest.json 里的 key 不带尾部 '='
        digest = hashlib.sha256(base64.b64decode(padded)).digest()[:16]
        expect = "".join(chr(ord("a") + int(c, 16)) for c in digest.hex())
        self.assertEqual(nh.EXTENSION_IDS[0], expect)

    def test_gecko_id_matches(self) -> None:
        self.assertEqual(self.manifest["browser_specific_settings"]["gecko"]["id"],
                         nh.GECKO_IDS[0])






class TestUninstall(TempHome):
    def test_removes_every_file(self) -> None:
        names = list(nh.BROWSERS["darwin"])
        self.seed("darwin", names)
        left_before = list(self.home.rglob(f"{nh.HOST_NAME}*.json"))
        self.assertTrue(left_before)
        self.assertTrue(self.wrapper().exists())
        nh.uninstall(self.home, "darwin")
        self.assertEqual(list(self.home.rglob(f"{nh.HOST_NAME}*.json")), [])
        self.assertFalse(self.wrapper().exists())
        self.assertFalse((self.home / nh.WRAPPER_DIR).exists())

    def test_reports_what_it_removed(self) -> None:
        self.seed("darwin", ["vivaldi"])
        removed = nh.uninstall(self.home, "darwin")
        self.assertEqual([n for n, _ in removed], ["vivaldi", "wrapper"])

    def test_is_safe_when_nothing_installed(self) -> None:
        self.assertEqual(nh.uninstall(self.home, "darwin"), [])

    def test_windows_deletes_keys_and_files(self) -> None:
        reg = FakeRegistry()
        names = list(nh.BROWSERS["win32"])
        self.seed("win32", names, reg_set=reg.set)
        nh.uninstall(self.home, "win32", reg_delete=reg.delete)
        self.assertEqual(reg.keys, {})
        self.assertIn(r"SOFTWARE\Mozilla\NativeMessagingHosts\com.lazygophers.browse",
                      reg.deleted)
        self.assertFalse((self.home / nh.WIN_MANIFEST_DIR).exists())

    def test_leaves_other_hosts_alone(self) -> None:
        self.seed("darwin", ["chrome"])
        other = self.home / nh._MAC_CHROME / "com.someone.else.json"
        other.write_text("{}", encoding="utf-8")
        nh.uninstall(self.home, "darwin")
        self.assertTrue(other.is_file())



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
            nh.main(["browse install", "--no-build", "--no-wait", *args])
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
            (REPO_ROOT / "browser-extension" / "browse" / "src" / "manifest.json").read_text()
        )
        gecko_id = manifest["browser_specific_settings"]["gecko"]["id"]
        self.assertIn(gecko_id, nh.GECKO_IDS)



class TestInstallStatus(TempHome):
    """`install_status`：链路三环（manifest → wrapper → browse）逐环查。"""

    def row(self, plat: str = "darwin", browser: str = "chrome") -> dict:
        rows = nh.install_status(self.home, plat)
        return next(r for r in rows if r["browser"] == browser)

    def test_nothing_installed_is_unregistered_not_stale(self) -> None:
        row = self.row()
        self.assertFalse(row["detected"])
        self.assertFalse(row["registered"])
        self.assertEqual(row["stale"], [], "从没装过不是断链")

    def test_healthy_chain_registers(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome")
        real_browse = self.seed("darwin", ["chrome"])
        row = self.row()
        self.assertTrue(row["detected"])
        self.assertTrue(row["registered"])
        self.assertEqual(row["stale"], [])
        manifest = row["manifests"][0]
        self.assertTrue(manifest["ok"])
        self.assertEqual(manifest["browse_path"], str(real_browse))

    def test_browse_moved_after_install_breaks_the_chain(self) -> None:
        """wrapper 里写死的 browse 被挪走：注册了但断链 —— 这正是 status 要抓的。"""
        self.touch_dir("Library/Application Support/Google/Chrome")
        tmp = self.seed("darwin", ["chrome"])
        tmp.unlink()
        row = self.row()
        self.assertFalse(row["registered"])
        self.assertEqual(len(row["stale"]), 1)
        manifest = row["manifests"][0]
        self.assertTrue(manifest["wrapper_ok"])
        self.assertFalse(manifest["browse_ok"])

    def test_corrupt_manifest_counts_as_stale_not_crash(self) -> None:
        self.touch_dir("Library/Application Support/Google/Chrome/NativeMessagingHosts")
        manifest = self.home / nh.BROWSERS["darwin"]["chrome"][2][0] / f"{nh.HOST_NAME}.json"
        manifest.write_text("not json", encoding="utf-8")
        row = self.row()
        self.assertFalse(row["registered"])
        self.assertEqual(row["stale"], [str(manifest)])

    def test_windows_registry_path_is_probed_via_the_injectable(self) -> None:
        """Windows 读注册表拿 manifest 路径：键没了 = 没注册，键在但文件没了 = 断链。"""
        def fake_query(key: str) -> str | None:
            return str(self.home / "nowhere.json") if "Google" in key else None

        rows = nh.install_status(self.home, "win32", reg_query=fake_query)
        chrome = next(r for r in rows if r["browser"] == "chrome")
        self.assertFalse(chrome["registered"])
        self.assertEqual(len(chrome["stale"]), 1, "键指向的文件不在 = 断链")
        firefox = next(r for r in rows if r["browser"] == "firefox")
        self.assertFalse(firefox["registered"])
        self.assertEqual(firefox["stale"], [], "键本身没有 = 没注册")


class TestWaitForExtension(unittest.TestCase):
    """回归：`wait_for_extension` 不能假设 `sys.argv[0]` 是 browse 自己。

    `lazyhelp install` 在同一个解释器里直接喊这个函数，这时 argv[0] 是
    bin/lazyhelp 的路径，不是 bin/browse——之前硬编 argv[0] 会去跑
    `lazyhelp browsingContext getTree`（lazyhelp 根本不认这个子命令），
    第一次探测就失败，表现成「没等就直接超时」。
    """

    def test_probes_the_resolved_browse_binary_not_argv0(self) -> None:
        done = subprocess.CompletedProcess([], 0)
        with unittest.mock.patch.object(sys, "argv", ["/somewhere/bin/lazyhelp", "install"]), \
             unittest.mock.patch("lib.lazyhelp._resolve", return_value="/repo/bin/browse") as resolve, \
             unittest.mock.patch.object(nh.subprocess, "run", return_value=done) as run:
            connected = nh.wait_for_extension(5.0)
        self.assertTrue(connected)
        resolve.assert_called_once_with("browse")
        called_cmd = run.call_args[0][0]
        self.assertEqual(called_cmd[0], "/repo/bin/browse")
        self.assertNotIn("/somewhere/bin/lazyhelp", called_cmd)

    def test_falls_back_to_bare_browse_when_unresolvable(self) -> None:
        """PATH 上也找不到时（理论上不该发生）别整个炸掉，退回裸名字让 shell 报错更直观。"""
        done = subprocess.CompletedProcess([], 0)
        with unittest.mock.patch("lib.lazyhelp._resolve", return_value=None), \
             unittest.mock.patch.object(nh.subprocess, "run", return_value=done) as run:
            connected = nh.wait_for_extension(5.0)
        self.assertTrue(connected)
        self.assertEqual(run.call_args[0][0][0], "browse")

    def test_times_out_without_hanging_past_deadline(self) -> None:
        not_connected = subprocess.CompletedProcess([], 3)  # EXIT_NO_BROWSER：正常等待态
        with unittest.mock.patch("lib.lazyhelp._resolve", return_value="/repo/bin/browse"), \
             unittest.mock.patch.object(nh.subprocess, "run", return_value=not_connected), \
             unittest.mock.patch.object(nh.time, "sleep") as sleep:
            connected = nh.wait_for_extension(0.01)
        self.assertFalse(connected)
        sleep.assert_called()  # 真走了轮询，不是探测一次就放弃
