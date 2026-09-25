"""lib/cli/ovpn.py 的子命令测试：分派、参数校验、返回码、root 门禁。

tests/test_ovpn.py 覆盖的是 lib/ovpn.py 里的纯函数（TOTP、协议解析、配置读写）；
这里跑的是 CLI 层：哪个子命令会提权、参数写错返回几、错误路径提示了什么。

所有外部动作都是假的——require_root / openvpn / 进程扫描全部 patch 掉，
配置文件只写 tempfile.TemporaryDirectory()，绝不碰用户 HOME，也不真跑 sudo。
"""

from __future__ import annotations

import contextlib
import functools
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import ovpn as ov
from lib.cli import ovpn as oc


class CliCase(unittest.TestCase):
    """公共脚手架：假 Reporter + 挡掉提权 + 内存配置。"""

    def setUp(self) -> None:
        self.cli = oc.OvpnCli()
        self.cli._r = mock.MagicMock()

        # require_root 真跑会 execvp 成 sudo，把测试进程本身替换掉。
        self.require_root = mock.patch.object(oc, "require_root").start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(oc, "secure_config", return_value=False).start()

        # 配置只活在内存里：load 读它，save 原样写回，测完即消失。
        self.cfg: dict = {}
        mock.patch.object(oc, "load_config", side_effect=lambda: dict(self.cfg)).start()
        mock.patch.object(oc, "save_config",
                          side_effect=lambda data: self.cfg.update(data)).start()

    def _stdout(self, fn, *args, **kwargs) -> tuple[int, str]:
        """跑一个子命令，返回 (退出码, stdout 文本)。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = fn(*args, **kwargs)
        return rc, buf.getvalue()

    def _kv(self) -> dict:
        """取最后一次 self._r.kv(title, rows) 的 rows。"""
        return self.cli._r.kv.call_args[0][1]


class TestNeedRoot(CliCase):
    """_need_root：提权 + 旧文件收归 root。"""

    def test_passes_original_argv_to_require_root(self) -> None:
        # 提权重跑的必须是用户敲的原始 argv（剥掉 argv[0]），不是被 run_cli 动过的
        with mock.patch.object(oc, "ORIG_ARGV", ["ovpn", "show", "--debug"]):
            self.cli._need_root("show")
        self.require_root.assert_called_once_with(oc.SCRIPT_PATH, ["show", "--debug"])

    def test_returns_false_so_caller_continues(self) -> None:
        self.assertFalse(self.cli._need_root("show"))

    def test_reports_when_legacy_config_is_secured(self) -> None:
        with mock.patch.object(oc, "secure_config", return_value=True):
            self.cli._need_root("show")
        self.assertIn("root:0600", self.cli._r.info.call_args[0][0])


class TestRootGate(CliCase):
    """哪些子命令碰配置 → 必须先提权；status 不读配置，普通用户就能跑。"""

    def test_config_touching_commands_require_root(self) -> None:
        self.cfg = {"totp_secret": "AAAA", "config": "/x.ovpn",
                    "username": "u", "password": "p"}
        cases = {
            "show": lambda: self.cli.show(),
            "code": lambda: self.cli.code(),
            "route": lambda: self.cli.route(),
            "disconnect": lambda: self.cli.disconnect(),
            "connect": lambda: self.cli.connect(),
        }
        with mock.patch.object(oc, "totp", return_value="123456"), \
             mock.patch.object(oc, "do_disconnect", return_value=0), \
             mock.patch.object(oc, "do_connect", return_value=0):
            for name, call in cases.items():
                with self.subTest(cmd=name):
                    self.require_root.reset_mock()
                    self._stdout(call)
                    self.require_root.assert_called_once()

    def test_status_does_not_require_root(self) -> None:
        with mock.patch.object(oc, "running_processes", return_value=[]), \
             mock.patch.object(oc, "tun_interfaces", return_value=[]):
            self.cli.status()
        self.require_root.assert_not_called()


class TestStatus(CliCase):
    """status：有进程返回 0，没进程返回 1。"""

    def test_no_process_is_rc_1(self) -> None:
        with mock.patch.object(oc, "running_processes", return_value=[]), \
             mock.patch.object(oc, "tun_interfaces", return_value=[]):
            rc = self.cli.status()
        self.assertEqual(rc, 1)
        self.cli._r.warn.assert_called_once()

    def test_running_process_is_rc_0(self) -> None:
        with mock.patch.object(oc, "running_processes",
                               return_value=[(42, "openvpn --config a.ovpn")]), \
             mock.patch.object(oc, "tun_interfaces", return_value=[("utun4", "10.8.0.2")]):
            rc = self.cli.status()
        self.assertEqual(rc, 0)
        self.assertIn("pid 42", self.cli._r.output.call_args[0][0])

    def test_tun_list_falls_back_to_placeholder(self) -> None:
        with mock.patch.object(oc, "running_processes", return_value=[]), \
             mock.patch.object(oc, "tun_interfaces", return_value=[]):
            self.cli.status()
        self.assertIn("(无)", self._kv())


class TestShow(CliCase):
    """show：没配置返回 1；有配置把密码和密钥打码。"""

    def test_missing_config_is_rc_1(self) -> None:
        rc = self.cli.show()
        self.assertEqual(rc, 1)
        self.assertIn("ovpn login", self.cli._r.warn.call_args[0][0])

    def test_secrets_are_masked(self) -> None:
        self.cfg = {"config": "/vpn/a.ovpn", "username": "alice",
                    "password": "hunter2secret", "totp_secret": "ABCDEFGH"}
        rc = self.cli.show()
        rows = self._kv()
        self.assertEqual(rc, 0)
        self.assertNotIn("hunter2secret", rows["密码"])
        self.assertNotIn("ABCDEFGH", rows["二步验证密钥"])
        self.assertEqual(rows["用户名"], "alice")

    def test_split_mode_off_without_rules(self) -> None:
        self.cfg = {"config": "/vpn/a.ovpn", "username": "u", "password": "p"}
        self.cli.show()
        self.assertIn("全量走 VPN", self._kv()["分流模式"])

    def test_split_mode_on_with_rules(self) -> None:
        self.cfg = {"config": "/vpn/a.ovpn", "username": "u", "password": "p",
                    "routes": {"domains": ["example.com"], "cidrs": []}}
        self.cli.show()
        self.assertIn("开", self._kv()["分流模式"])

    def test_split_disabled_in_config_wins(self) -> None:
        self.cfg = {"config": "/vpn/a.ovpn", "username": "u", "password": "p",
                    "split_tunnel": False,
                    "routes": {"domains": ["example.com"], "cidrs": []}}
        self.cli.show()
        self.assertIn("split_tunnel=false", self._kv()["分流模式"])


class TestCode(CliCase):
    """code：只打验证码，不连 VPN。"""

    def test_missing_secret_is_rc_1(self) -> None:
        rc = self.cli.code()
        self.assertEqual(rc, 1)
        self.assertIn("ovpn login", self.cli._r.err.call_args[0][0])

    def test_prints_code_to_stdout(self) -> None:
        self.cfg = {"totp_secret": "ABCDEFGH"}
        with mock.patch.object(oc, "totp", return_value="654321"):
            rc, out = self._stdout(self.cli.code)
        self.assertEqual((rc, out.strip()), (0, "654321"))

    def test_broken_secret_is_rc_2(self) -> None:
        self.cfg = {"totp_secret": "!!!"}
        with mock.patch.object(oc, "totp", side_effect=ValueError("密钥不是合法 base32")):
            rc, _ = self._stdout(self.cli.code)
        self.assertEqual(rc, 2)
        self.assertIn("base32", self.cli._r.err.call_args[0][0])


class TestRouteList(CliCase):
    """route list：只读，不写配置。"""

    def test_default_action_is_list(self) -> None:
        self.cfg = {"routes": {"domains": ["a.com"], "cidrs": ["10.8.0.0/16"]}}
        rc = self.cli.route()
        rows = self._kv()
        self.assertEqual(rc, 0)
        self.assertEqual(rows["域名"], "a.com")
        self.assertEqual(rows["网段"], "10.8.0.0/16")

    def test_list_does_not_write_config(self) -> None:
        self.cli.route("list")
        oc.save_config.assert_not_called()

    def test_empty_rules_show_placeholder(self) -> None:
        self.cli.route("list")
        self.assertEqual(self._kv()["域名"], "(无)")

    def test_dns_port_is_persisted_even_on_list(self) -> None:
        self.cli.route("list", dns_port=5399)
        self.assertEqual(self.cfg["dns_port"], 5399)
        self.assertEqual(self._kv()["DNS 代理端口"], "5399")

    def test_default_dns_port_when_unset(self) -> None:
        self.cli.route("list")
        self.assertEqual(self._kv()["DNS 代理端口"], str(oc.DEFAULT_DNS_PORT))


class TestRouteAdd(CliCase):
    """route add：按格式把规则分到域名 / 网段两堆。"""

    def test_wildcard_domain_is_normalized(self) -> None:
        self.cli.route("add", "*.example.com")
        self.assertEqual(self.cfg["routes"]["domains"], ["example.com"])

    def test_cidr_goes_to_cidrs(self) -> None:
        self.cli.route("add", "10.8.0.0/16")
        self.assertEqual(self.cfg["routes"], {"domains": [], "cidrs": ["10.8.0.0/16"]})

    def test_host_address_is_normalized_to_network(self) -> None:
        # strict=False：单个 IP 归一成 /32 网段
        self.cli.route("add", "10.8.0.7")
        self.assertEqual(self.cfg["routes"]["cidrs"], ["10.8.0.7/32"])

    def test_duplicate_domain_is_not_added_twice(self) -> None:
        self.cfg = {"routes": {"domains": ["example.com"], "cidrs": []}}
        self.cli.route("add", ".example.com")
        self.assertEqual(self.cfg["routes"]["domains"], ["example.com"])
        self.cli._r.info.assert_called_once()

    def test_duplicate_cidr_is_not_added_twice(self) -> None:
        self.cfg = {"routes": {"domains": [], "cidrs": ["10.8.0.0/16"]}}
        self.cli.route("add", "10.8.0.0/16")
        self.assertEqual(self.cfg["routes"]["cidrs"], ["10.8.0.0/16"])
        self.cli._r.info.assert_called_once()

    def test_unparseable_rule_is_skipped_with_warning(self) -> None:
        rc = self.cli.route("add", "   ")
        self.assertEqual(rc, 0)
        self.assertIn("看不懂的规则", self.cli._r.warn.call_args[0][0])
        self.assertEqual(self.cfg["routes"], {"domains": [], "cidrs": []})

    def test_add_without_items_is_rc_1(self) -> None:
        rc = self.cli.route("add")
        self.assertEqual(rc, 1)
        oc.save_config.assert_not_called()

    def test_writes_audit_event(self) -> None:
        with mock.patch.object(oc.slog, "record") as rec:
            self.cli.route("add", "example.com")
        # timed_cli 自己也会写 cli.start / cli.done，挑出 ovpn.route 那条
        routed = [c for c in rec.call_args_list if c[0][0] == "ovpn.route"]
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0][1]["op"], "add")
        self.assertEqual(routed[0][1]["domains"], ["example.com"])


class TestRouteRemoveClear(CliCase):
    """route remove / clear。"""

    def setUp(self) -> None:
        super().setUp()
        self.cfg = {"routes": {"domains": ["example.com"], "cidrs": ["10.8.0.0/16"]}}

    def test_remove_domain(self) -> None:
        self.cli.route("remove", "*.example.com")
        self.assertEqual(self.cfg["routes"]["domains"], [])
        self.assertEqual(self.cfg["routes"]["cidrs"], ["10.8.0.0/16"])

    def test_remove_cidr(self) -> None:
        self.cli.route("remove", "10.8.0.0/16")
        self.assertEqual(self.cfg["routes"]["cidrs"], [])

    def test_remove_unknown_rule_warns(self) -> None:
        self.cli.route("remove", "nope.com")
        self.cli._r.warn.assert_called_once()
        self.assertEqual(self.cfg["routes"]["domains"], ["example.com"])

    def test_remove_without_items_is_rc_1(self) -> None:
        rc = self.cli.route("remove")
        self.assertEqual(rc, 1)
        oc.save_config.assert_not_called()

    def test_cidr_removal_matches_by_prefix(self) -> None:
        # 现状：网段用 startswith 匹配，写个前缀就能删掉整条（见最终报告里的 bug 记录）
        self.cli.route("remove", "10.8")
        self.assertEqual(self.cfg["routes"]["cidrs"], [])

    def test_clear_empties_both_lists(self) -> None:
        rc = self.cli.route("clear")
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg["routes"], {"domains": [], "cidrs": []})

    def test_unknown_action_is_rc_1(self) -> None:
        rc = self.cli.route("delete", "example.com")
        self.assertEqual(rc, 1)
        self.assertIn("未知动作", self.cli._r.err.call_args[0][0])
        oc.save_config.assert_not_called()


class TestConnect(CliCase):
    """connect：配置齐了才连，缺了先转 login。"""

    FULL = {"config": "/vpn/a.ovpn", "username": "u", "password": "p"}

    def test_delegates_to_lib_connect(self) -> None:
        self.cfg = dict(self.FULL)
        with mock.patch.object(oc, "do_connect", return_value=0) as do:
            rc = self.cli.connect(verbose=True, reconnect=False, reconnect_max=3)
        self.assertEqual(rc, 0)
        self.assertEqual(do.call_args[1],
                         {"verbose": True, "reconnect": False, "reconnect_max": 3})

    def test_incomplete_config_falls_back_to_login(self) -> None:
        self.cfg = {"config": "/vpn/a.ovpn"}
        with mock.patch.object(self.cli, "login", return_value=0) as login, \
             mock.patch.object(oc, "do_connect", return_value=0):
            self.cli.connect()
        login.assert_called_once()
        self.assertIn("username", self.cli._r.warn.call_args[0][0])

    def test_failed_login_aborts_connect(self) -> None:
        with mock.patch.object(self.cli, "login", return_value=1), \
             mock.patch.object(oc, "do_connect") as do:
            rc = self.cli.connect()
        self.assertEqual(rc, 1)
        do.assert_not_called()

    def test_no_split_disables_split_tunnel_for_this_run(self) -> None:
        self.cfg = dict(self.FULL, routes={"domains": ["a.com"], "cidrs": []})
        with mock.patch.object(oc, "do_connect", return_value=0) as do:
            self.cli.connect(split=False)
        self.assertIs(do.call_args[0][0]["split_tunnel"], False)
        self.assertNotIn("split_tunnel", self.cfg)  # 只影响本次，不落盘

    def test_ctrl_c_is_rc_130(self) -> None:
        self.cfg = dict(self.FULL)
        with mock.patch.object(oc, "do_connect", side_effect=KeyboardInterrupt):
            rc = self.cli.connect()
        self.assertEqual(rc, 130)


class TestDisconnect(CliCase):
    def test_returns_lib_disconnect_rc(self) -> None:
        with mock.patch.object(oc, "do_disconnect", return_value=7) as do:
            rc = self.cli.disconnect()
        self.assertEqual(rc, 7)
        do.assert_called_once_with(self.cli._r)


class TestLogin(CliCase):
    """login：逐项询问，任一项取消就整体放弃。"""

    def _answers(self, *, profile="/vpn/a.ovpn", username="alice",
                 password="pw", has_totp=False, secret=""):
        texts = [profile, username, password] + ([secret] if has_totp else [])
        return (mock.patch.object(oc, "ask_text", side_effect=texts),
                mock.patch.object(oc, "ask_confirm", return_value=has_totp))

    def test_writes_all_four_fields(self) -> None:
        text, confirm = self._answers()
        with text, confirm:
            rc = self.cli.login()
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg, {"config": "/vpn/a.ovpn", "username": "alice",
                                    "password": "pw", "totp_secret": ""})

    def test_totp_secret_is_normalized_and_verified(self) -> None:
        text, confirm = self._answers(has_totp=True, secret="otpauth://totp/x?secret=ABCDEFGH")
        with text, confirm, \
             mock.patch.object(oc, "normalize_secret", return_value="ABCDEFGH"), \
             mock.patch.object(oc, "totp", return_value="111222"):
            rc = self.cli.login()
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg["totp_secret"], "ABCDEFGH")
        self.assertIn("111222", self.cli._r.ok.call_args_list[0][0][0])

    def test_bad_totp_secret_is_rc_2_and_writes_nothing(self) -> None:
        text, confirm = self._answers(has_totp=True, secret="!!!")
        with text, confirm, \
             mock.patch.object(oc, "normalize_secret", return_value="!!!"), \
             mock.patch.object(oc, "totp", side_effect=ValueError("密钥不是合法 base32")):
            rc = self.cli.login()
        self.assertEqual(rc, 2)
        oc.save_config.assert_not_called()

    def test_missing_profile_file_only_warns(self) -> None:
        text, confirm = self._answers(profile="/does/not/exist.ovpn")
        with text, confirm:
            rc = self.cli.login()
        self.assertEqual(rc, 0)
        self.assertIn("文件当前不存在", self.cli._r.warn.call_args[0][0])

    def test_cancel_at_any_prompt_is_rc_1(self) -> None:
        # ask_text 返回 None = 用户 Ctrl-C / Ctrl-D；三个文本输入各试一遍
        for idx in range(3):
            with self.subTest(prompt=idx):
                answers = ["/vpn/a.ovpn", "alice", "pw"]
                answers[idx] = None
                with mock.patch.object(oc, "ask_text", side_effect=answers), \
                     mock.patch.object(oc, "ask_confirm", return_value=False):
                    rc = self.cli.login()
                self.assertEqual(rc, 1)
                oc.save_config.assert_not_called()

    def test_cancel_at_totp_question_is_rc_1(self) -> None:
        with mock.patch.object(oc, "ask_text", side_effect=["/vpn/a.ovpn", "alice", "pw"]), \
             mock.patch.object(oc, "ask_confirm", return_value=None):
            rc = self.cli.login()
        self.assertEqual(rc, 1)
        oc.save_config.assert_not_called()

    def test_cancel_at_secret_prompt_is_rc_1(self) -> None:
        with mock.patch.object(oc, "ask_text", side_effect=["/vpn/a.ovpn", "alice", "pw", None]), \
             mock.patch.object(oc, "ask_confirm", return_value=True):
            rc = self.cli.login()
        self.assertEqual(rc, 1)
        oc.save_config.assert_not_called()

    def test_empty_answer_keeps_existing_value(self) -> None:
        # 密码那一项直接回车（空串）→ 沿用配置里已有的值
        self.cfg = {"config": "/vpn/a.ovpn", "username": "alice", "password": "old"}
        with mock.patch.object(oc, "ask_text", side_effect=["/vpn/a.ovpn", "alice", ""]), \
             mock.patch.object(oc, "ask_confirm", return_value=False):
            self.cli.login()
        self.assertEqual(self.cfg["password"], "old")


class TestLoginRoundTrip(unittest.TestCase):
    """login 真写一遍 YAML：文件落到临时目录，权限 0600。"""

    def test_config_file_is_written_0600(self) -> None:
        cli = oc.OvpnCli()
        cli._r = mock.MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ovpn.yaml"
            with mock.patch.object(oc, "require_root"), \
                 mock.patch.object(oc, "secure_config", return_value=False), \
                 mock.patch.object(oc, "load_config",
                                   functools.partial(ov.load_config, path)), \
                 mock.patch.object(oc, "save_config",
                                   lambda data: ov.save_config(data, path)), \
                 mock.patch.object(oc, "ask_text", side_effect=[str(path), "alice", "pw"]), \
                 mock.patch.object(oc, "ask_confirm", return_value=False):
                rc = cli.login()
            self.assertEqual(rc, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("alice", path.read_text(encoding="utf-8"))


class TestAskSecret(unittest.TestCase):
    """_ask_secret：默认明文回显；SCRIPTS_HIDE_SECRET=1 切回不回显。"""

    def test_default_path_uses_ask_text(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCRIPTS_HIDE_SECRET", None)
            with mock.patch.object(oc, "ask_text", return_value="  pw  ") as t:
                self.assertEqual(oc._ask_secret("密码"), "pw")
            t.assert_called_once()

    def test_hidden_path_uses_rich_prompt(self) -> None:
        from rich.prompt import Prompt
        with mock.patch.dict(os.environ, {"SCRIPTS_HIDE_SECRET": "1"}), \
             mock.patch.object(Prompt, "ask", return_value=" secret ") as ask:
            self.assertEqual(oc._ask_secret("密码"), "secret")
        self.assertTrue(ask.call_args[1]["password"])

    def test_hidden_empty_answer_falls_back_to_default(self) -> None:
        from rich.prompt import Prompt
        with mock.patch.dict(os.environ, {"SCRIPTS_HIDE_SECRET": "1"}), \
             mock.patch.object(Prompt, "ask", return_value=""):
            self.assertEqual(oc._ask_secret("密码", default="old"), "old")


class TestMain(unittest.TestCase):
    def test_main_hands_a_cli_to_run_cli(self) -> None:
        with mock.patch.object(oc, "run_cli") as run:
            oc.main()
        self.assertIsInstance(run.call_args[0][0], oc.OvpnCli)


class TestDispatch(CliCase):
    """__call__：裸调用 → connect；带名字 → 按顺序跑。"""

    def test_bare_call_runs_connect(self) -> None:
        with mock.patch.object(self.cli, "connect", return_value=0) as c:
            rc = self.cli()
        self.assertEqual(rc, 0)
        c.assert_called_once_with(verbose=False, reconnect=True, reconnect_max=0, split=True)

    def test_bare_call_forwards_connect_flags(self) -> None:
        with mock.patch.object(self.cli, "connect", return_value=0) as c:
            self.cli(verbose=True, reconnect_max=5)
        self.assertEqual(c.call_args[1]["verbose"], True)
        self.assertEqual(c.call_args[1]["reconnect_max"], 5)

    def test_unknown_subcommand_is_rc_1(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self.cli("nosuchthing")
        self.assertEqual(rc, 1)
        self.assertIn("未知子命令", buf.getvalue())

    def test_runs_commands_in_order(self) -> None:
        order = []
        with mock.patch.object(self.cli, "status", side_effect=lambda: order.append("status")), \
             mock.patch.object(self.cli, "show", side_effect=lambda: order.append("show")):
            rc = self.cli("status", "show")
        self.assertEqual((rc, order), (0, ["status", "show"]))

    def test_stops_at_first_failure(self) -> None:
        with mock.patch.object(self.cli, "status", return_value=3), \
             mock.patch.object(self.cli, "show") as show:
            rc = self.cli("status", "show")
        self.assertEqual(rc, 3)
        show.assert_not_called()


if __name__ == "__main__":
    unittest.main()
