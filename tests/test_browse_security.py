"""browse 安全层测试：三模式、只能收紧、拒绝名单、审计权限位与保留期。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_protocol import ERR_USER_REJECTED  # noqa: E402
from lib.browse_redact import REDACTED  # noqa: E402
from lib.browse_security import (  # noqa: E402
    CONFIRM_MODES,
    RISKY_ACTIONS,
    RISKY_METHODS,
    Security,
    SecurityError,
    config_view,
    default_config_path,
    domain_matches,
    domain_of,
    load_config,
    resolve_confirm_mode,
    risky_action,
    sanitize_config,
    save_config,
    state_dir,
    target_url,
    update_config,
)

# 扩展侧 handlers/confirm.ts:14-24 的 RiskyAction 联合类型，逐字抄过来
EXTENSION_ACTIONS = {
    "readCookies", "writeCookies", "readLocalStorage", "writeLocalStorage",
    "evalMainWorld", "download", "readHistory", "writeHistory",
    "readBookmarks", "writeBookmarks",
}


class Temp(unittest.TestCase):
    """每个用例一份临时的配置文件 + 审计目录，绝不碰真实家目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.config_path = self.root / "browse.yaml"
        self.audit_dir = self.root / "state" / "browse"

    def make(self, **cfg) -> Security:
        return Security(cfg, config_path=self.config_path, audit_dir=self.audit_dir)


class TestRiskyActions(Temp):
    def test_actions_match_extension_union(self):
        self.assertEqual(RISKY_ACTIONS, EXTENSION_ACTIONS)

    def test_every_risky_method_maps(self):
        for method, action in RISKY_METHODS.items():
            with self.subTest(method):
                self.assertEqual(risky_action(method), action)

    def test_harmless_methods_are_not_risky(self):
        for method in ("browsingContext.navigate", "browsingContext.captureScreenshot",
                       "input.click", "network.subscribe", "lg:downloads.list"):
            with self.subTest(method):
                self.assertIsNone(risky_action(method))

    def test_js_locator_is_main_world(self):
        # spec 4.4：`js=` 定位在 MAIN world 求值，也算高危 —— 只看方法名会漏。
        # 字段名必须是 `selector`：CLI 的位置参数名（lib/cli/browse.py:82）和扩展侧读
        # 的键（handlers/input.ts:65）都叫这个，读别的名字等于这条判定永远不触发。
        self.assertEqual(risky_action("input.click", {"selector": "js=document.body"}),
                         "evalMainWorld")
        self.assertEqual(risky_action("input.type", {"selector": "js=x", "text": "y"}),
                         "evalMainWorld")
        self.assertIsNone(risky_action("input.click", {"selector": "css=button"}))

    def test_js_selector_reaches_the_confirm_flow(self):
        """`js=` 不只是被标成高危，还真的会走到确认（而不是被静默放行）。"""
        sec = self.make(confirm_mode="always")
        self.assertEqual(
            sec.check("input.click", {"selector": "js=document.body"}),
            {"action": "evalMainWorld", "method": "input.click", "url": None},
        )


class TestUrlAndDomain(Temp):
    def test_target_url_prefers_url_then_domain(self):
        self.assertEqual(target_url({"url": "https://a.com/x"}), "https://a.com/x")
        self.assertEqual(target_url({"domain": ".b.com"}), ".b.com")
        self.assertIsNone(target_url({}))
        self.assertIsNone(target_url(None))

    def test_domain_of(self):
        self.assertEqual(domain_of("https://Example.COM/login?a=1"), "example.com")
        self.assertEqual(domain_of(".example.com"), "example.com")
        self.assertEqual(domain_of("example.com:8080"), "example.com")
        self.assertIsNone(domain_of(None))
        self.assertIsNone(domain_of(""))

    def test_domain_matches_covers_subdomains(self):
        self.assertTrue(domain_matches("a.example.com", "example.com"))
        self.assertTrue(domain_matches("a.example.com", "*.example.com"))
        self.assertTrue(domain_matches("example.com", "example.com"))
        self.assertFalse(domain_matches("notexample.com", "example.com"))
        self.assertFalse(domain_matches(None, "example.com"))
        self.assertFalse(domain_matches("example.com", ""))


class TestConfirmModes(Temp):
    COOKIES = ("storage.getCookies", {"url": "https://example.com/"})

    def test_default_is_silent(self):
        self.assertEqual(Security({}, config_path=self.config_path,
                                  audit_dir=self.audit_dir).confirm_mode, "silent")

    def test_silent_never_asks(self):
        self.assertIsNone(self.make(confirm_mode="silent").check(*self.COOKIES))

    def test_always_asks_every_time(self):
        sec = self.make(confirm_mode="always")
        for _ in range(3):
            self.assertEqual(sec.check(*self.COOKIES),
                             {"action": "readCookies", "method": "storage.getCookies",
                              "url": "https://example.com/"})

    def test_always_ignores_approvals(self):
        sec = self.make(confirm_mode="always", approved_domains=["example.com"])
        self.assertIsNotNone(sec.check(*self.COOKIES))

    def test_per_domain_asks_once_then_remembers(self):
        sec = self.make(confirm_mode="per_domain")
        self.assertIsNotNone(sec.check(*self.COOKIES))
        sec.approve("example.com")
        self.assertIsNone(sec.check(*self.COOKIES))

    def test_per_domain_approval_is_revocable(self):
        sec = self.make(confirm_mode="per_domain")
        sec.approve("example.com")
        self.assertEqual(sec.approvals(), ["example.com"])
        sec.revoke("example.com")
        self.assertEqual(sec.approvals(), [])
        self.assertIsNotNone(sec.check(*self.COOKIES))

    def test_approval_persists_to_disk(self):
        self.make(confirm_mode="per_domain").approve("example.com")
        self.assertEqual(load_config(self.config_path)["approved_domains"], ["example.com"])

    def test_approval_of_parent_does_not_cover_subdomain(self):
        # 批准 example.com 不该连 evil.example.com 一起放行
        sec = self.make(confirm_mode="per_domain")
        sec.approve("example.com")
        self.assertIsNotNone(sec.check("storage.getCookies", {"url": "https://evil.example.com/"}))

    def test_harmless_commands_never_ask(self):
        sec = self.make(confirm_mode="always")
        self.assertIsNone(sec.check("browsingContext.navigate", {"url": "https://example.com/"}))


class TestOnlyTighten(Temp):
    """命令行只能收紧不能放宽 —— 防被入侵的脚本自己把确认关掉。"""

    def test_always_rejects_silent_override(self):
        with self.assertRaises(SecurityError) as ctx:
            resolve_confirm_mode("always", "silent")
        self.assertIn("只能收紧", str(ctx.exception))

    def test_always_rejects_per_domain_override(self):
        with self.assertRaises(SecurityError):
            resolve_confirm_mode("always", "per_domain")

    def test_per_domain_rejects_silent_override(self):
        with self.assertRaises(SecurityError):
            resolve_confirm_mode("per_domain", "silent")

    def test_tightening_is_allowed(self):
        self.assertEqual(resolve_confirm_mode("silent", "always"), "always")
        self.assertEqual(resolve_confirm_mode("silent", "per_domain"), "per_domain")
        self.assertEqual(resolve_confirm_mode("per_domain", "always"), "always")

    def test_same_mode_is_allowed(self):
        for mode in CONFIRM_MODES:
            with self.subTest(mode):
                self.assertEqual(resolve_confirm_mode(mode, mode), mode)

    def test_no_override_keeps_config(self):
        self.assertEqual(resolve_confirm_mode("always", ""), "always")
        self.assertEqual(resolve_confirm_mode(None, None), "silent")

    def test_security_constructor_enforces_it(self):
        with self.assertRaises(SecurityError):
            Security({"confirm_mode": "always"}, override_mode="silent",
                     config_path=self.config_path, audit_dir=self.audit_dir)

    def test_illegal_modes_rejected(self):
        with self.assertRaises(SecurityError) as ctx:
            resolve_confirm_mode("off")
        self.assertEqual(ctx.exception.code, "invalid argument")
        with self.assertRaises(SecurityError):
            resolve_confirm_mode("silent", "off")


class TestDenyDomains(Temp):
    def test_default_deny_list_is_empty(self):
        self.assertEqual(self.make().deny_domains, [])

    def test_denied_domain_raises_user_rejected(self):
        sec = self.make(deny_domains=["bank.com"])
        with self.assertRaises(SecurityError) as ctx:
            sec.check("browsingContext.navigate", {"url": "https://bank.com/transfer"})
        self.assertEqual(ctx.exception.code, ERR_USER_REJECTED)
        self.assertIn("bank.com", str(ctx.exception))

    def test_deny_covers_subdomains(self):
        sec = self.make(deny_domains=["bank.com"])
        with self.assertRaises(SecurityError):
            sec.check("storage.getCookies", {"url": "https://login.bank.com/"})

    def test_deny_beats_silent_mode(self):
        # 静默模式不是「全放行」，拒绝名单照样拦
        sec = self.make(confirm_mode="silent", deny_domains=["bank.com"])
        with self.assertRaises(SecurityError):
            sec.check("storage.getCookies", {"domain": "bank.com"})

    def test_other_domains_pass(self):
        sec = self.make(deny_domains=["bank.com"])
        self.assertIsNone(sec.check("browsingContext.navigate", {"url": "https://example.com/"}))

    def test_page_methods_need_a_lookup_only_when_the_list_is_not_empty(self):
        """`input.*` / `script.*` 的 params 里没有 url —— 目标页得先问扩展。"""
        self.assertTrue(self.make(deny_domains=["bank.com"])
                        .needs_target_lookup("input.click", {"selector": "text=转账"}))
        self.assertTrue(self.make(deny_domains=["bank.com"])
                        .needs_target_lookup("script.evaluate", {"expression": "1"}))
        # 名单空着就不问：一条没得拦，多一个往返纯属白花
        self.assertFalse(self.make().needs_target_lookup("input.click", {}))
        # 自带 url 的不用问
        self.assertFalse(self.make(deny_domains=["bank.com"])
                         .needs_target_lookup("browsingContext.navigate",
                                              {"url": "https://a.com/"}))
        # 浏览器全局的（列书签、搜历史）根本不落在某一页上
        self.assertFalse(self.make(deny_domains=["bank.com"])
                         .needs_target_lookup("lg:bookmarks.search", {"query": "x"}))

    def test_deny_applies_to_the_url_the_caller_resolved(self):
        sec = self.make(deny_domains=["bank.com"])
        with self.assertRaises(SecurityError):
            sec.check("input.click", {"selector": "text=转账"}, url="https://bank.com/x")


class TestAudit(Temp):
    def read_lines(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_records_who_did_what_where(self):
        sec = self.make()
        path = sec.audit("storage.getCookies", params={"url": "https://example.com/x"},
                         pid=4321, result="success", elapsed_ms=12.34)
        entry = self.read_lines(path)[0]
        self.assertEqual(entry["method"], "storage.getCookies")
        self.assertEqual(entry["domain"], "example.com")
        self.assertEqual(entry["action"], "readCookies")
        self.assertEqual(entry["pid"], 4321)
        self.assertEqual(entry["result"], "success")
        self.assertEqual(entry["ms"], 12.3)
        self.assertTrue(entry["ts"])

    def test_one_line_per_command(self):
        sec = self.make()
        for _ in range(3):
            path = sec.audit("browsingContext.navigate", params={"url": "https://a.com/"})
        self.assertEqual(len(self.read_lines(path)), 3)

    def test_filename_is_dated(self):
        sec = self.make()
        path = sec.audit("browsingContext.navigate")
        self.assertEqual(path.name, f"audit-{datetime.date.today().isoformat()}.jsonl")

    def test_permissions_are_0700_dir_0600_file(self):
        sec = self.make()
        path = sec.audit("browsingContext.navigate")
        self.assertEqual(stat.S_IMODE(os.stat(self.audit_dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_permissions_survive_loose_umask(self):
        old = os.umask(0)
        self.addCleanup(os.umask, old)
        path = self.make().audit("browsingContext.navigate")
        self.assertEqual(stat.S_IMODE(os.stat(self.audit_dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_no_page_content_or_cookie_values(self):
        sec = self.make()
        path = sec.audit("storage.getCookies",
                         params={"url": "https://example.com/", "value": "SESSIONID=deadbeef",
                                 "text": "页面正文不该出现"})
        entry = self.read_lines(path)[0]
        self.assertNotIn("value", entry)
        self.assertNotIn("text", entry)
        self.assertNotIn("deadbeef", path.read_text(encoding="utf-8"))

    def test_error_message_is_redacted_before_truncation(self):
        sec = self.make()
        path = sec.audit("script.evaluate", params={"url": "https://a.com/"}, result="error",
                         error="登录失败 token=sk-ant-api03-" + "Z" * 200)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-", text)
        self.assertIn(REDACTED, text)

    def test_can_be_disabled(self):
        sec = self.make(audit=False)
        self.assertIsNone(sec.audit("storage.getCookies"))
        self.assertFalse(self.audit_dir.exists())

    def test_browser_wide_command_has_no_domain(self):
        sec = self.make()
        path = sec.audit("lg:bookmarks.search")
        self.assertIsNone(self.read_lines(path)[0]["domain"])


class TestRetention(Temp):
    def seed(self, *days_ago):
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today()
        made = []
        for n in days_ago:
            path = self.audit_dir / f"audit-{(today - datetime.timedelta(days=n)).isoformat()}.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            made.append(path)
        return made

    def test_default_keeps_seven_days(self):
        fresh, edge, old = self.seed(0, 7, 8)
        self.make().prune()
        self.assertTrue(fresh.exists())
        self.assertTrue(edge.exists())
        self.assertFalse(old.exists())

    def test_retention_is_configurable(self):
        fresh, old = self.seed(1, 3)
        self.make(audit_retention_days=2).prune()
        self.assertTrue(fresh.exists())
        self.assertFalse(old.exists())

    def test_zero_means_keep_forever(self):
        (old,) = self.seed(400)
        self.assertEqual(self.make(audit_retention_days=0).prune(), [])
        self.assertTrue(old.exists())

    def test_foreign_files_untouched(self):
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        other = self.audit_dir / "audit-not-a-date.jsonl"
        other.write_text("", encoding="utf-8")
        self.make().prune()
        self.assertTrue(other.exists())

    def test_writing_prunes_once(self):
        (old,) = self.seed(30)
        self.make().audit("browsingContext.navigate")
        self.assertFalse(old.exists())

    def test_bad_retention_value_falls_back_to_default(self):
        (old,) = self.seed(30)
        self.make(audit_retention_days="七天").prune()
        self.assertFalse(old.exists())


class TestDefaultPaths(Temp):
    def test_state_dir_shape(self):
        self.assertEqual(state_dir(), pathlib.Path.home()
                         / ".local" / "state" / "lazygophers" / "scripts" / "browse")

    def test_config_lives_with_the_other_tools(self):
        self.assertEqual(default_config_path(), pathlib.Path.home()
                         / ".config" / "lazygophers" / "scripts" / "browse.yaml")


class TestConfigFile(Temp):
    def test_written_config_is_0600(self):
        save_config({"confirm_mode": "always"}, self.config_path)
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)

    def test_missing_config_loads_defaults(self):
        sec = Security(config_path=self.root / "nope.yaml", audit_dir=self.audit_dir)
        self.assertEqual(sec.confirm_mode, "silent")
        self.assertEqual(sec.deny_domains, [])
        self.assertEqual(sec.approvals(), [])

    def test_round_trip_from_disk(self):
        save_config({"confirm_mode": "per_domain", "deny_domains": ["bank.com"]},
                    self.config_path)
        sec = Security(config_path=self.config_path, audit_dir=self.audit_dir)
        self.assertEqual(sec.confirm_mode, "per_domain")
        self.assertEqual(sec.deny_domains, ["bank.com"])

    def test_approve_rereads_concurrent_change(self):
        # 另一个进程先写了一条，approve 必须在锁内重读，不能覆盖掉它
        sec = self.make(confirm_mode="per_domain")
        save_config({"approved_domains": ["other.com"]}, self.config_path)
        sec.approve("example.com")
        self.assertEqual(load_config(self.config_path)["approved_domains"],
                         ["other.com", "example.com"])


class TestConfigView(Temp):
    """设置页照着 `config_view` 画界面，所以缺字段时它得给出真正在生效的默认值。"""

    def test_empty_config_shows_the_real_defaults(self):
        self.assertEqual(config_view({}), {
            "confirm_mode": "silent",
            "deny_domains": [],
            "approved_domains": [],
            "audit": True,
            "audit_retention_days": 7,
        })
        self.assertEqual(config_view(None), config_view({}))

    def test_values_from_the_file_win(self):
        view = config_view({"confirm_mode": "always", "audit": False,
                            "audit_retention_days": 0, "deny_domains": ["a.test"]})
        self.assertEqual(view["confirm_mode"], "always")
        self.assertIs(view["audit"], False)
        self.assertEqual(view["audit_retention_days"], 0)
        self.assertEqual(view["deny_domains"], ["a.test"])

    def test_a_garbage_retention_falls_back_instead_of_blowing_up(self):
        self.assertEqual(config_view({"audit_retention_days": "七天"})["audit_retention_days"], 7)


class TestSanitizeConfig(Temp):
    """设置页送来的东西一律当不可信输入：扩展是能被改的，socket 也不是只有它能连。"""

    def test_only_the_five_known_fields_survive(self):
        got = sanitize_config({"audit": False, "token": "secret", "confirm_mode": "always"})
        self.assertEqual(got, {"audit": False, "confirm_mode": "always"})

    def test_absent_fields_are_left_alone(self):
        self.assertEqual(sanitize_config({}), {})

    def test_domains_are_normalised_and_deduped(self):
        got = sanitize_config({"deny_domains": ["*.Bank.test", " ", ".bank.test", "shop.test"]})
        self.assertEqual(got["deny_domains"], ["bank.test", "shop.test"])

    def test_an_illegal_confirm_mode_raises(self):
        for mode in ("loud", "", "SILENT"):
            with self.assertRaises(SecurityError):
                sanitize_config({"confirm_mode": mode})

    def test_illegal_types_raise(self):
        for patch in ({"deny_domains": "bank.test"}, {"deny_domains": [1]},
                      {"approved_domains": {"a": 1}}, {"audit": "yes"}, {"audit": 1},
                      {"audit_retention_days": "7"}, {"audit_retention_days": 1.5}):
            with self.assertRaises(SecurityError, msg=str(patch)):
                sanitize_config(patch)

    def test_a_bool_is_not_a_retention_count(self):
        # bool 是 int 的子类：不挡住的话 `true` 会被静静地当成「保留 1 天」
        with self.assertRaises(SecurityError):
            sanitize_config({"audit_retention_days": True})

    def test_zero_and_negative_retention_are_legal(self):
        self.assertEqual(sanitize_config({"audit_retention_days": 0})["audit_retention_days"], 0)
        self.assertEqual(sanitize_config({"audit_retention_days": -1})["audit_retention_days"], -1)


class TestUpdateConfig(Temp):
    def test_untouched_fields_survive_including_unknown_ones(self):
        save_config({"approved_domains": ["shop.test"], "future_field": 1}, self.config_path)
        cfg = update_config({"confirm_mode": "always"}, self.config_path)
        self.assertEqual(cfg["approved_domains"], ["shop.test"])
        self.assertEqual(cfg["future_field"], 1)
        self.assertEqual(load_config(self.config_path)["confirm_mode"], "always")

    def test_an_illegal_patch_writes_nothing(self):
        with self.assertRaises(SecurityError):
            update_config({"confirm_mode": "loud"}, self.config_path)
        self.assertFalse(self.config_path.exists(), "校验没过就一个字都不该落盘")

    def test_the_written_file_is_still_0600(self):
        update_config({"audit": False}, self.config_path)
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)

    def test_it_rereads_under_the_lock(self):
        # 另一个进程在这中间写了一条，不能被整份覆盖掉
        save_config({"deny_domains": ["a.test"]}, self.config_path)
        update_config({"audit": False}, self.config_path)
        self.assertEqual(load_config(self.config_path)["deny_domains"], ["a.test"])


if __name__ == "__main__":
    unittest.main()
