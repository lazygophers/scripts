"""浏览器企业策略：落点、合并、清理、以及 sudo 失败时的降级。

**这里一次都不会真的调 sudo，也一次都不会写 /Library 或 /etc。** 落点全部经
`mac_root` / `linux_root` 指到临时目录，`apply()` 的 `runner` 用假的。真去写系统文件的
测试没法在 CI 上跑，而且跑错一次就改了跑测试那个人的机器。
"""

from __future__ import annotations

import io
import json
import pathlib
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_policy as bp  # noqa: E402

IDS = ("podeceeeafjdcemppcgjhhokcokpcama",)
OURS = {
    "installation_mode": "allowed",
    "toolbar_pin": "force_pinned",
    "file_url_navigation_allowed": True,
}


class Temp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)

    def mac_plan(self, browsers=("chrome",), **kw):
        return bp.plan("darwin", browsers, IDS, mac_root=self.root, **kw)

    def write_plist(self, name: str, data: dict) -> pathlib.Path:
        path = self.root / name
        path.write_bytes(plistlib.dumps(data))
        return path


# ---------------------------------------------------------------- 落点
class TestTargets(Temp):
    """每个浏览器的落点各断言一条。抄错一个 bundle id，策略就写进一个没人读的文件。"""

    def test_macos_bundle_ids(self):
        got = {t.browser: t.path.name
               for t in bp.targets("darwin", bp.MAC_DOMAINS, mac_root=self.root)}
        self.assertEqual(got, {
            "chrome": "com.google.Chrome.plist",
            "chromium": "org.chromium.Chromium.plist",
            "edge": "com.microsoft.Edge.plist",
            "brave": "com.brave.Browser.plist",
            "opera": "com.operasoftware.Opera.plist",
            "vivaldi": "com.vivaldi.Vivaldi.plist",
        })

    def test_macos_real_directory(self):
        """默认落点必须是 /Library/Managed Preferences，别的目录 Chromium 不读。"""
        target = bp.targets("darwin", ["chrome"])[0]
        self.assertEqual(str(target.path),
                         "/Library/Managed Preferences/com.google.Chrome.plist")
        self.assertEqual(target.fmt, "plist")

    def test_linux_managed_dirs(self):
        got = {t.browser: str(t.path) for t in bp.targets("linux", bp.LINUX_DIRS)}
        self.assertEqual(got, {
            "chrome": "/etc/opt/chrome/policies/managed/lazygophers-browse.json",
            "chromium": "/etc/chromium/policies/managed/lazygophers-browse.json",
            "edge": "/etc/opt/edge/policies/managed/lazygophers-browse.json",
            "brave": "/etc/brave/policies/managed/lazygophers-browse.json",
            "opera": "/etc/opt/opera/policies/managed/lazygophers-browse.json",
            "vivaldi": "/etc/opt/vivaldi/policies/managed/lazygophers-browse.json",
        })
        self.assertTrue(all(t.fmt == "json" for t in bp.targets("linux", bp.LINUX_DIRS)))

    def test_firefox_is_deliberately_absent(self):
        """Firefox 不写：企业策略绕不过 Mozilla 签名，写了也装不上。

        出处 <https://extensionworkshop.com/documentation/enterprise/enterprise-distribution/>。
        而且它的落点在 app 包里，改那个还要拆 quarantine —— 拿用户的浏览器冒险。
        """
        for plat in ("darwin", "linux"):
            self.assertEqual(bp.targets(plat, ["firefox"]), [])

    def test_windows_writes_nothing(self):
        """策略落点是 HKLM 注册表，这台机器上验不了，就不写没验过的代码。"""
        self.assertEqual(bp.targets("win32", list(bp.MAC_DOMAINS)), [])
        self.assertEqual(bp.plan("win32", list(bp.MAC_DOMAINS), IDS), [])

    def test_unknown_browser_is_skipped_not_crashed(self):
        self.assertEqual(bp.targets("darwin", ["netscape"], mac_root=self.root), [])


# ---------------------------------------------------------------- 内容
class TestContent(Temp):
    def test_the_three_fields_we_promise(self):
        self.assertEqual(bp.settings_for(IDS), {IDS[0]: OURS})

    def test_no_force_installed_and_no_incognito(self):
        """写死这两条，免得以后有人「顺手加上」。

        force_installed 要 update_url + 一个我们签不出来的 CRX；incognito 在
        ExtensionSettings 的策略定义里根本不存在。
        """
        entry = bp.settings_for(IDS)[IDS[0]]
        self.assertNotEqual(entry["installation_mode"], "force_installed")
        self.assertNotIn("incognito", entry)
        self.assertNotIn("update_url", entry)

    def test_plan_writes_a_readable_plist(self):
        steps = self.mac_plan()
        (target, body), = steps
        self.assertEqual(target.path.name, "com.google.Chrome.plist")
        self.assertEqual(plistlib.loads(body), {"ExtensionSettings": {IDS[0]: OURS}})

    def test_plan_writes_json_on_linux(self):
        steps = bp.plan("linux", ["chrome"], IDS, linux_root=self.root)
        (_, body), = steps
        self.assertEqual(json.loads(body), {"ExtensionSettings": {IDS[0]: OURS}})

    def test_several_extension_ids_each_get_an_entry(self):
        ids = ("aaa", "bbb")
        self.assertEqual(set(bp.settings_for(ids)), set(ids))


# ---------------------------------------------------------------- 合并
class TestMerge(Temp):
    """macOS 一个域名只有一个 plist，公司 MDM 的策略也在里面。覆盖 = 抹掉别人的。"""

    def test_other_policies_survive(self):
        self.write_plist("com.google.Chrome.plist", {
            "HomepageLocation": "https://intra.corp",
            "ExtensionSettings": {"otherext": {"installation_mode": "blocked"}},
        })
        (_, body), = self.mac_plan()
        got = plistlib.loads(body)
        self.assertEqual(got["HomepageLocation"], "https://intra.corp")
        self.assertEqual(got["ExtensionSettings"]["otherext"],
                         {"installation_mode": "blocked"})
        self.assertEqual(got["ExtensionSettings"][IDS[0]], OURS)

    def test_rewriting_our_own_entry_is_idempotent(self):
        self.write_plist("com.google.Chrome.plist", {"ExtensionSettings": {IDS[0]: OURS}})
        self.assertEqual(self.mac_plan(), [], "已经是想要的样子就不该再要管理员密码")

    def test_an_unreadable_file_is_treated_as_empty_not_fatal(self):
        (self.root / "com.google.Chrome.plist").write_bytes(b"not a plist at all")
        (_, body), = self.mac_plan()
        self.assertEqual(plistlib.loads(body), {"ExtensionSettings": {IDS[0]: OURS}})

    def test_a_non_dict_extension_settings_is_replaced_not_crashed(self):
        self.write_plist("com.google.Chrome.plist", {"ExtensionSettings": "junk"})
        (_, body), = self.mac_plan()
        self.assertEqual(plistlib.loads(body)["ExtensionSettings"], {IDS[0]: OURS})


# ---------------------------------------------------------------- 清理
class TestRemove(Temp):
    def test_uninstall_deletes_the_file_we_created(self):
        self.write_plist("com.google.Chrome.plist", {"ExtensionSettings": {IDS[0]: OURS}})
        (target, body), = self.mac_plan(remove=True)
        self.assertIsNone(body, "只剩我们那条，整个文件该删掉")
        self.assertEqual(target.path.name, "com.google.Chrome.plist")

    def test_uninstall_leaves_other_policies_alone(self):
        self.write_plist("com.google.Chrome.plist", {
            "HomepageLocation": "https://intra.corp",
            "ExtensionSettings": {IDS[0]: OURS, "otherext": {"installation_mode": "blocked"}},
        })
        (_, body), = self.mac_plan(remove=True)
        got = plistlib.loads(body)
        self.assertEqual(got["HomepageLocation"], "https://intra.corp")
        self.assertEqual(got["ExtensionSettings"], {"otherext": {"installation_mode": "blocked"}})

    def test_uninstall_when_nothing_was_written_does_nothing(self):
        self.assertEqual(self.mac_plan(remove=True), [])

    def test_uninstall_leaves_no_trace_of_us(self):
        """装 → 卸之后，文件里不该再有我们的扩展 ID。"""
        (target, body), = self.mac_plan()
        target.path.write_bytes(body)
        (target2, body2), = self.mac_plan(remove=True)
        self.assertIsNone(body2)
        # 落点确实是刚写的那个文件
        self.assertEqual(target2.path, target.path)


# ---------------------------------------------------------------- sudo
class FakeRun:
    """假的 subprocess.run。记下命令，按 `code` 决定成不成。"""

    def __init__(self, code: int = 0, stderr: str = ""):
        self.code = code
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return mock.Mock(returncode=self.code, stdout="", stderr=self.stderr)


class TestApply(Temp):
    def test_one_sudo_for_all_files(self):
        """每个落点弹一次密码是最烦人的做法，所以只起一次 sudo。"""
        steps = bp.plan("darwin", list(bp.MAC_DOMAINS), IDS, mac_root=self.root)
        self.assertEqual(len(steps), 6)
        run = FakeRun()
        bp.apply(steps, runner=run)
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][:3], ["sudo", "/bin/sh", "-c"])

    def test_the_script_writes_every_target_as_root_owned_0644(self):
        steps = self.mac_plan()
        run = FakeRun()
        bp.apply(steps, runner=run)
        script = run.calls[0][3]
        dest = str(steps[0][0].path)
        self.assertIn(f"mkdir -p {self.root}", script)
        self.assertIn(f"> {dest}", script)
        self.assertIn(f"chmod 644 {dest}", script)
        self.assertIn(f"chown root {dest}", script)
        self.assertTrue(script.startswith("set -e"), "中间一步失败必须停下来")

    def test_removal_script_deletes(self):
        self.write_plist("com.google.Chrome.plist", {"ExtensionSettings": {IDS[0]: OURS}})
        steps = self.mac_plan(remove=True)
        run = FakeRun()
        bp.apply(steps, runner=run)
        self.assertIn("rm -f ", run.calls[0][3])

    def test_a_refused_sudo_raises_and_says_why(self):
        """用户按了 Ctrl-C 或密码错了 —— 必须抛，不许静默当成功。"""
        run = FakeRun(code=1, stderr="sudo: 3 incorrect password attempts")
        with self.assertRaises(bp.PolicyError) as caught:
            bp.apply(self.mac_plan(), runner=run)
        self.assertIn("incorrect password", str(caught.exception))

    def test_a_silent_failure_still_carries_the_exit_code(self):
        with self.assertRaises(bp.PolicyError) as caught:
            bp.apply(self.mac_plan(), runner=FakeRun(code=77))
        self.assertIn("77", str(caught.exception))

    def test_nothing_to_do_never_calls_sudo(self):
        run = FakeRun()
        bp.apply([], runner=run)
        self.assertEqual(run.calls, [], "没有要改的东西就不该弹密码框")

    def test_no_sudo_binary_is_reported_not_raised(self):
        def boom(argv, **kw):
            raise OSError("no such file: sudo")

        self.assertFalse(bp.sudo_available(runner=boom))
        self.assertTrue(bp.sudo_available(runner=FakeRun()))
        self.assertFalse(bp.sudo_available(runner=FakeRun(code=1)))


# ---------------------------------------------------------------- 降级
class TestInstallFallback(Temp):
    """策略没写成时 `browse install` 的表现：说清楚少了什么，然后照常走手动加载。"""

    def setUp(self):
        super().setUp()
        from lib import browse_install

        self.mod = browse_install
        self.out = mock.Mock()

    def test_a_failed_policy_returns_false_and_says_so(self):
        with mock.patch.object(bp, "plan", return_value=self.mac_plan()), \
             mock.patch.object(bp, "sudo_available", return_value=True), \
             mock.patch.object(bp, "apply", side_effect=bp.PolicyError("用户取消了")):
            ok = self.mod.write_policy(self.out, "darwin", ["chrome"], IDS)
        self.assertFalse(ok)
        said = " ".join(str(c) for c in self.out.err.call_args_list)
        self.assertIn("用户取消了", said)

    def test_missing_sudo_is_reported_and_apply_is_never_called(self):
        with mock.patch.object(bp, "plan", return_value=self.mac_plan()), \
             mock.patch.object(bp, "sudo_available", return_value=False), \
             mock.patch.object(bp, "apply") as apply_mock:
            ok = self.mod.write_policy(self.out, "darwin", ["chrome"], IDS)
        self.assertFalse(ok)
        apply_mock.assert_not_called()

    def test_the_plan_is_printed_before_the_password_is_asked_for(self):
        order: list[str] = []
        self.out.info.side_effect = lambda msg: order.append(f"info:{msg}")
        with mock.patch.object(bp, "plan", return_value=self.mac_plan()), \
             mock.patch.object(bp, "sudo_available", return_value=True), \
             mock.patch.object(bp, "apply", side_effect=lambda *a, **k: order.append("sudo")):
            self.mod.write_policy(self.out, "darwin", ["chrome"], IDS)
        self.assertIn("sudo", order)
        printed = order[:order.index("sudo")]
        self.assertTrue(any("com.google.Chrome.plist" in line for line in printed),
                        "要密码之前必须先说清楚要动哪个文件")
        self.assertTrue(any("管理员密码" in line for line in printed))
        self.assertTrue(any("browse uninstall" in line for line in printed),
                        "要密码之前必须先说清楚怎么撤销")

    def test_the_fallback_notice_lists_what_is_lost(self):
        self.mod.policy_fallback_notice(self.out)
        said = " ".join(str(c) for c in self.out.warn.call_args_list)
        for want in self.mod.POLICY_WANTS:
            self.assertIn(want, said)

    def test_nothing_to_change_is_success_not_failure(self):
        with mock.patch.object(bp, "plan", return_value=[]), \
             mock.patch.object(bp, "apply") as apply_mock:
            self.assertTrue(self.mod.write_policy(self.out, "darwin", ["chrome"], IDS))
        apply_mock.assert_not_called()


class Tty(io.StringIO):
    """假的 stderr：`isatty()` 说真话就走交互那半边，但一个字都不会真的写出去。"""

    def isatty(self) -> bool:
        return True


class TestInstallWiring(Temp):
    """`browse install` 真的会调到策略这一步吗 —— 前面那些测试全是直接调函数，
    接错线的话它们照样绿。这里从 `main()` 进，把 sudo 那一层换成假的。"""

    def setUp(self):
        super().setUp()
        from lib import browse_install

        self.mod = browse_install
        self.home = self.root / "home"
        (self.home / "Library/Application Support/Google/Chrome").mkdir(parents=True)

    def run_install(self, *args, apply_mock=None):
        applied = apply_mock or mock.Mock()
        with mock.patch.object(self.mod.pathlib.Path, "home", staticmethod(lambda: self.home)), \
             mock.patch.object(self.mod, "platform_key", lambda *a: "darwin"), \
             mock.patch.object(self.mod, "copy_to_clipboard", lambda text: False), \
             mock.patch.object(bp, "sudo_available", return_value=True), \
             mock.patch.object(bp, "apply", applied), \
             mock.patch.object(bp, "MAC_POLICY_DIR", str(self.root / "Managed")), \
             mock.patch("sys.stderr", new=Tty()) as err:
            code = self.mod.main(["browse install", "--browsers", "chrome",
                                  "--no-build", "--no-wait", *args])
        return code, applied, err.getvalue()

    def test_install_writes_the_policy(self):
        code, applied, out = self.run_install()
        self.assertEqual(code, self.mod.EXIT_OK)
        applied.assert_called_once()
        steps = applied.call_args[0][0]
        self.assertEqual([t.path.name for t, _ in steps], ["com.google.Chrome.plist"])
        self.assertIn("管理员密码", out)

    def test_no_policy_skips_it_entirely(self):
        code, applied, _ = self.run_install("--no-policy")
        self.assertEqual(code, self.mod.EXIT_OK)
        applied.assert_not_called()

    def test_a_refused_sudo_still_finishes_the_manual_flow(self):
        """用户按了 Ctrl-C：不能静默继续，也不能就此失败。"""
        refused = mock.Mock(side_effect=bp.PolicyError("用户取消"))
        code, _, out = self.run_install(apply_mock=refused)
        self.assertEqual(code, self.mod.EXIT_OK, "策略没写成不该让整条安装失败")
        self.assertIn("用户取消", out)
        for want in self.mod.POLICY_WANTS:
            self.assertIn(want[:8], out, "必须说清楚因此少了什么")
        self.assertIn("chrome://extensions", out, "还是要把手动加载的步骤给出来")

    def test_the_incognito_limit_is_told_not_hidden(self):
        """无痕模式策略里没有这个字段，做不了。不许假装装完就万事大吉。"""
        _, _, out = self.run_install()
        self.assertIn("无痕", out)
        self.assertIn("只能你自己点", out)

    def test_uninstall_cleans_the_policy_up(self):
        applied = mock.Mock()
        (self.root / "Managed").mkdir()
        (self.root / "Managed" / "com.google.Chrome.plist").write_bytes(
            plistlib.dumps({"ExtensionSettings": {IDS[0]: OURS}}))
        with mock.patch.object(self.mod.pathlib.Path, "home", staticmethod(lambda: self.home)), \
             mock.patch.object(self.mod, "platform_key", lambda *a: "darwin"), \
             mock.patch.object(bp, "sudo_available", return_value=True), \
             mock.patch.object(bp, "apply", applied), \
             mock.patch.object(bp, "MAC_POLICY_DIR", str(self.root / "Managed")), \
             mock.patch("sys.stderr", new=Tty()):
            self.assertEqual(self.mod.main(["browse install", "--uninstall"]),
                             self.mod.EXIT_OK)
        applied.assert_called_once()
        steps = applied.call_args[0][0]
        self.assertEqual([body for _, body in steps], [None], "只剩我们那条，文件该整个删掉")


if __name__ == "__main__":
    unittest.main()
