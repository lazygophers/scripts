"""grafana CLI 层测试：子命令分派、参数校验、返回码、错误路径。

全部在进程内跑（不起 HTTP、不写用户 HOME）：`lib.cli.grafana` 把
`client_for` / `load_config` / `save_config` 等名字导进了自己的模块命名空间，
所以一律 patch `lib.cli.grafana.<name>`。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli import grafana as gcli  # noqa: E402
from lib.grafana import GrafanaError  # noqa: E402

M = "lib.cli.grafana"


def run(fn, *args, **kwargs) -> tuple[int, str]:
    """跑一个 CLI 方法，返回 (退出码, stdout)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


def cli() -> gcli.GrafanaCli:
    """构造一个 reporter 被替换成 mock 的 CLI（断言提示文案，且不污染 stderr）。"""
    c = gcli.GrafanaCli(mock.MagicMock())
    return c


def messages(reporter_mock, level: str) -> list[str]:
    return [call.args[0] for call in getattr(reporter_mock, level).call_args_list]


class TestEmit(unittest.TestCase):
    """emit：None 静默、字符串原样、其余压缩 JSON。"""

    def test_none_prints_nothing(self):
        self.assertEqual(run(gcli.emit, None)[1], "")

    def test_str_printed_verbatim(self):
        self.assertEqual(run(gcli.emit, "hello")[1], "hello\n")

    def test_dict_printed_as_json(self):
        out = run(gcli.emit, {"a": 1})[1]
        self.assertEqual(json.loads(out), {"a": 1})


class TestParseSince(unittest.TestCase):
    """--since 解析：合法单位转纳秒，其余报错。"""

    def test_units(self):
        self.assertEqual(gcli._parse_since("30s"), 30 * 10**9)
        self.assertEqual(gcli._parse_since("2m"), 120 * 10**9)
        self.assertEqual(gcli._parse_since(" 24H "), 24 * 3600 * 10**9)
        self.assertEqual(gcli._parse_since("7d"), 7 * 86400 * 10**9)

    def test_garbage_raises(self):
        with self.assertRaises(GrafanaError):
            gcli._parse_since("一会儿")


class TestCmdDecorator(unittest.TestCase):
    """@cmd：GrafanaError 被收敛成 rc=2 + 一行 err，不往外抛。"""

    def test_grafana_error_becomes_rc2(self):
        c = cli()
        with mock.patch.object(gcli, "client_for", side_effect=GrafanaError("没登录")):
            rc, _ = run(c.health)
        self.assertEqual(rc, 2)
        self.assertIn("没登录", messages(c._r, "err"))


class TestHosts(unittest.TestCase):
    """hosts：空配置 rc=1 并指路 login；有配置逐行列出并标记 current。"""

    def test_empty_config_exits_1_and_points_to_login(self):
        c = cli()
        with mock.patch.object(gcli, "load_config", return_value={}), \
             mock.patch.object(gcli, "profiles", return_value={}), \
             mock.patch.object(gcli, "default_config_path", return_value=pathlib.Path("/tmp/x.yaml")):
            rc, out = run(c.hosts)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("grafana login", messages(c._r, "warn")[0])

    def test_lists_profiles_with_current_marker(self):
        c = cli()
        cfg = {"current": "b.com"}
        known = {"a.com": {"url": "https://a.com"}, "b.com": {"url": "https://b.com"}}
        with mock.patch.object(gcli, "load_config", return_value=cfg), \
             mock.patch.object(gcli, "profiles", return_value=known):
            rc, _ = run(c.hosts)
        self.assertEqual(rc, 0)
        lines = messages(c._r, "step")
        self.assertEqual(lines, ["  a.com -> https://a.com", "★ b.com -> https://b.com"])

    def test_ai_shell_prints_plain_lines_to_stdout(self):
        # AI 环境走极简出口：直接 print 纯文本，current 用 "* " 标记
        c = cli()
        cfg = {"current": "b.com"}
        known = {"a.com": {"url": "https://a.com"}, "b.com": {}}
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), \
             mock.patch.object(gcli, "load_config", return_value=cfg), \
             mock.patch.object(gcli, "profiles", return_value=known):
            rc, out = run(c.hosts)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "a.com -> https://a.com\n* b.com -> b.com\n")
        c._r.step.assert_not_called()


class TestUse(unittest.TestCase):
    """use：把解析出来的 key 写进 current 并落盘。"""

    def test_switch_writes_current(self):
        c = cli()
        saved = {}
        with mock.patch.object(gcli, "config_lock"), \
             mock.patch.object(gcli, "load_config", return_value={}), \
             mock.patch.object(gcli, "resolve_profile", return_value=("b.com", {"url": "https://b.com"})), \
             mock.patch.object(gcli, "put_profile", side_effect=lambda cfg, k, p: {**cfg, "profiles": {k: p}}), \
             mock.patch.object(gcli, "save_config", side_effect=saved.update):
            rc, _ = run(c.use, "b.com")
        self.assertEqual(rc, 0)
        self.assertEqual(saved["current"], "b.com")
        self.assertEqual(saved["profiles"], {"b.com": {"url": "https://b.com"}})

    def test_unknown_host_becomes_rc2(self):
        c = cli()
        with mock.patch.object(gcli, "config_lock"), \
             mock.patch.object(gcli, "load_config", return_value={}), \
             mock.patch.object(gcli, "resolve_profile", side_effect=GrafanaError("没有这个站点")):
            rc, _ = run(c.use, "nope.com")
        self.assertEqual(rc, 2)


class TestLogin(unittest.TestCase):
    """login：参数齐全直接落盘；缺参数走 ask_text；用户放弃则 rc=1。"""

    def setUp(self):
        self.saved = {}
        self.patches = [
            mock.patch.object(gcli, "config_lock"),
            mock.patch.object(gcli, "load_config", return_value={}),
            mock.patch.object(gcli, "put_profile", side_effect=lambda cfg, k, p: {**cfg, "profiles": {k: p}}),
            mock.patch.object(gcli, "save_config", side_effect=self.saved.update),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_token_login_saves_profile(self):
        c = cli()
        rc, _ = run(c.login, url="https://g.example.com/d/abc", token="tok")
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved["current"], "g.example.com")
        profile = self.saved["profiles"]["g.example.com"]
        self.assertEqual(profile["url"], "https://g.example.com")
        self.assertEqual(profile["token"], "tok")
        self.assertIs(profile["insecure"], False)

    def test_host_flag_is_alias_of_url(self):
        c = cli()
        rc, _ = run(c.login, host="https://h.example.com", token="tok")
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved["current"], "h.example.com")

    def test_prompts_for_url_then_token(self):
        c = cli()
        with mock.patch.object(gcli, "ask_text", side_effect=["https://p.example.com", "tok"]) as ask:
            rc, _ = run(c.login)
        self.assertEqual(rc, 0)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(self.saved["profiles"]["p.example.com"]["token"], "tok")

    def test_empty_url_cancels(self):
        c = cli()
        with mock.patch.object(gcli, "ask_text", return_value="  "):
            rc, _ = run(c.login)
        self.assertEqual(rc, 1)
        self.assertIn("已取消", messages(c._r, "err"))
        self.assertEqual(self.saved, {})

    def test_falls_back_to_username_password(self):
        c = cli()
        # token 留空 → 依次问用户名、密码
        with mock.patch.object(gcli, "ask_text", side_effect=["", "nico", "pw"]):
            rc, _ = run(c.login, url="https://u.example.com")
        self.assertEqual(rc, 0)
        profile = self.saved["profiles"]["u.example.com"]
        self.assertEqual((profile["username"], profile["password"]), ("nico", "pw"))

    def test_empty_username_cancels(self):
        c = cli()
        with mock.patch.object(gcli, "ask_text", side_effect=["", "   "]):
            rc, _ = run(c.login, url="https://u.example.com")
        self.assertEqual(rc, 1)
        self.assertEqual(self.saved, {})

    def test_empty_password_cancels(self):
        c = cli()
        with mock.patch.object(gcli, "ask_text", side_effect=["", "nico", ""]):
            rc, _ = run(c.login, url="https://u.example.com")
        self.assertEqual(rc, 1)
        self.assertEqual(self.saved, {})


class ClientCase(unittest.TestCase):
    """共用：把 client_for 换成 mock 客户端。"""

    def setUp(self):
        self.client = mock.MagicMock()
        p = mock.patch.object(gcli, "client_for", return_value=self.client)
        p.start()
        self.addCleanup(p.stop)
        self.client_for = p


class TestPassthroughCommands(ClientCase):
    """health / search / api / auth state：调对方法并把结果打成 JSON。"""

    def test_health_prints_payload(self):
        self.client.health.return_value = {"database": "ok"}
        rc, out = run(cli().health)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"database": "ok"})

    def test_search_forwards_query(self):
        self.client.search.return_value = [{"title": "orders"}]
        rc, out = run(cli().search, "orders")
        self.assertEqual(rc, 0)
        self.client.search.assert_called_once_with("orders")
        self.assertEqual(json.loads(out), [{"title": "orders"}])

    def test_auth_state_uses_health(self):
        self.client.health.return_value = {"version": "11.0.0"}
        rc, out = run(cli().auth.state)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["version"], "11.0.0")

    def test_api_parses_data_and_extra_params(self):
        self.client.request.return_value = {"ok": True}
        rc, _ = run(cli().api, "POST", "/api/echo", data='{"a": 1}', orgId=2)
        self.assertEqual(rc, 0)
        self.client.request.assert_called_once_with(
            "POST", "/api/echo", json_body={"a": 1}, params={"orgId": 2})

    def test_api_without_data_sends_empty_body_and_no_params(self):
        self.client.request.return_value = None
        rc, out = run(cli().api, "GET", "/api/health")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.client.request.assert_called_once_with(
            "GET", "/api/health", json_body={}, params=None)

    def test_api_bad_json_becomes_rc2(self):
        rc, _ = run(cli().api, "POST", "/api/echo", data="不是 JSON")
        self.assertEqual(rc, 2)
        self.client.request.assert_not_called()

    def test_host_flag_reaches_client_for(self):
        self.client.health.return_value = {}
        run(cli().health, host="b.com")
        self.assertEqual(gcli.client_for.call_args.args[0], "b.com")


class TestLogs(ClientCase):
    """logs：时间窗口换算、filter 透传、空结果 rc=1。"""

    def test_formats_rows_and_passes_window(self):
        self.client.loki_logs.return_value = [(1789040867164282444, "m-1", "hello")]
        rc, out = run(cli().logs, '{service_name="x"}', filter=("keep",), n=5, since="30m")
        self.assertEqual(rc, 0)
        kwargs = self.client.loki_logs.call_args.kwargs
        self.assertEqual(kwargs["limit"], 5)
        self.assertEqual(kwargs["since_ns"], 30 * 60 * 10**9)
        self.assertEqual(kwargs["filters"], ("keep",))
        self.assertTrue(out.rstrip().endswith("m-1 hello"))

    def test_empty_result_warns_and_exits_1(self):
        c = cli()
        self.client.loki_logs.return_value = []
        rc, out = run(c.logs, "{}")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("没有匹配的日志", messages(c._r, "warn"))

    def test_bad_since_becomes_rc2(self):
        rc, _ = run(cli().logs, "{}", since="一天")
        self.assertEqual(rc, 2)
        self.client.loki_logs.assert_not_called()


class TestMain(unittest.TestCase):
    """main：`-h` 走短帮助并 exit 0；裸跑补 `--skills`。"""

    def test_help_exits_zero(self):
        with mock.patch.object(sys, "argv", ["grafana", "--help"]), \
             mock.patch.object(gcli, "_print_help") as help_mock, \
             self.assertRaises(SystemExit) as ctx:
            gcli.main()
        self.assertEqual(ctx.exception.code, 0)
        help_mock.assert_called_once()

    def test_bare_call_appends_skills(self):
        with mock.patch.object(sys, "argv", ["grafana"]), \
             mock.patch.object(gcli, "run_cli") as run_mock:
            gcli.main()
            self.assertEqual(sys.argv, ["grafana", "--skills"])
        run_mock.assert_called_once()

    def test_subcommand_argv_untouched(self):
        with mock.patch.object(sys, "argv", ["grafana", "health"]), \
             mock.patch.object(gcli, "run_cli"):
            gcli.main()
            self.assertEqual(sys.argv, ["grafana", "health"])


class TestPrintHelp(unittest.TestCase):
    """--help 短帮助列出常用子命令。"""

    def test_lists_common_commands(self):
        r = mock.MagicMock()
        with mock.patch.object(gcli, "reporter", return_value=r):
            gcli._print_help()
        rows = r.summary.call_args.args[1]
        self.assertEqual([name for name, _, _ in rows],
                         ["login", "hosts", "health", "search", "logs", "api"])


if __name__ == "__main__":
    unittest.main()
