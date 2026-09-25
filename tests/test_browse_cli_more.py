"""browse CLI 补测：路由报错、友好层分支、管理命令、daemon 接管。

不起真 daemon、不连浏览器：`execute` 换成一个假的协程（按方法名给假结果），
`ensure_daemon` / `probe` 直接当成「在跑」。只验 CLI 自己的判断和输出。
已有的 tests/test_browse_cli.py 走真 daemon + 假扩展，这里补它没覆盖到的分支。
"""

import asyncio
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli import browse  # noqa: E402


def call(tokens, handler=None, *, daemon=True):
    """跑一条 `browse <tokens>`，返回 (退出码, stdout, stderr, 调用过的方法列表)。"""
    calls: list[tuple[str, dict]] = []

    async def fake_execute(method, params, sock, duration=None, browser=""):
        calls.append((method, dict(params)))
        got = handler(method, params) if handler else {}
        if isinstance(got, dict) and "status" in got:
            return got
        return {"status": "ok", "result": {} if got is None else got}

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(browse, "execute", fake_execute), \
         mock.patch.object(browse, "ensure_daemon", return_value=daemon), \
         mock.patch.object(browse, "probe", return_value=daemon), \
         mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
        rc = browse._main(["browse", *tokens])
    return rc, out.getvalue(), err.getvalue(), calls


def error_json(text: str) -> dict:
    """stderr 上既有 reporter 的提示又有错误 JSON，挑出 JSON 那一行。"""
    for line in text.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"stderr 里没有错误对象: {text!r}")


def flat(text: str) -> str:
    """Reporter 会按宽度折行，断言文案前先把换行抹掉。"""
    return text.replace("\n", "")


def tabs(*rows) -> dict:
    """造 browsingContext.getTree 的返回：(context, url, active) 三元组。"""
    return {"contexts": [{"context": c, "parent": None, "url": u, "lg:title": u, "lg:active": a}
                         for c, u, a in rows]}


def groups(*rows) -> dict:
    """造 lg:tabs.groups 的返回：(group, title, window, tabs) 四元组。"""
    return {"groups": [{"group": g, "title": t, "window": w, "tabs": list(ids), "color": "blue"}
                       for g, t, w, ids in rows]}


class TestRouteErrors(unittest.TestCase):
    """路由层的三类参数错误，一律退出码 2。"""

    def test_api_needs_module_and_action(self):
        rc, _, err, _ = call(["api", "history"])
        self.assertEqual(rc, 2)
        self.assertIn("browse api <module> <action>", err)

    def test_unknown_action_in_noun_group_lists_the_group(self):
        rc, _, err, _ = call(["data", "没这个"])
        self.assertEqual(rc, 2)
        self.assertIn("没有这条指令: data 没这个", err)
        self.assertIn("cookies", err)

    def test_unknown_group_action_includes_the_special_ones(self):
        rc, _, err, _ = call(["group", "没这个"])
        self.assertEqual(rc, 2)
        self.assertIn("dissolve", err)  # group 组的五条特殊命令也要列出来

    def test_too_many_positionals(self):
        rc, _, err, _ = call(["open", "https://a.com", "多余的"])
        self.assertEqual(rc, 2)
        self.assertIn("最多吃 1 个位置参数", err)
        self.assertIn("多余的", err)


class TestPrintResultMinimal(unittest.TestCase):
    """AI 环境的表格降级：能拍平的出 TSV，嵌套的出压缩 JSON。"""

    def test_list_of_dicts_becomes_tsv(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            browse.print_result({"tabs": [{"id": "1", "url": "a"}, {"id": "2"}]},
                                table=True, out=out)
        self.assertEqual(out.getvalue(), "id\turl\n1\ta\n2\t\n")

    def test_nested_result_stays_json(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            browse.print_result({"info": {"pid": 3}}, table=True, out=out)
        self.assertEqual(json.loads(out.getvalue()), {"info": {"pid": 3}})


class TestKillOccupant(unittest.TestCase):
    """_kill_occupant：pid 对不上就不动手，交回调用方报「起不来」。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock = pathlib.Path(self.tmp.name) / "browse.sock"
        self.report = mock.MagicMock()

    def _write_pid(self, text: str):
        browse.pid_path(self.sock).write_text(text, encoding="utf-8")

    def test_missing_pid_file(self):
        self.assertIs(browse._kill_occupant(self.sock, self.report), False)

    def test_unreadable_pid_file(self):
        self._write_pid("不是数字")
        self.assertIs(browse._kill_occupant(self.sock, self.report), False)

    def test_ps_failure(self):
        self._write_pid("4242")
        with mock.patch("subprocess.run", side_effect=OSError):
            self.assertIs(browse._kill_occupant(self.sock, self.report), False)

    def test_pid_reused_by_another_process(self):
        self._write_pid("4242")
        with mock.patch("subprocess.run",
                        return_value=mock.MagicMock(stdout="/usr/bin/vim notes.md")):
            self.assertIs(browse._kill_occupant(self.sock, self.report), False)

    def test_sigterm_sent_to_the_right_daemon(self):
        self._write_pid("4242")
        command = f"python bridge run --socket {self.sock}"
        with mock.patch("subprocess.run", return_value=mock.MagicMock(stdout=command)), \
             mock.patch("os.kill") as kill:
            self.assertIs(browse._kill_occupant(self.sock, self.report), True)
        kill.assert_called_once()
        self.report.info.assert_called_once()

    def test_process_gone_before_sigterm_is_fine(self):
        self._write_pid("4242")
        command = f"python bridge run --socket {self.sock}"
        with mock.patch("subprocess.run", return_value=mock.MagicMock(stdout=command)), \
             mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertIs(browse._kill_occupant(self.sock, self.report), True)


class TestAdoptSocket(unittest.TestCase):
    """_adopt_socket：先等体面退出，超时再 SIGTERM 接管。"""

    def setUp(self):
        self.sock = pathlib.Path("/tmp/adopt.sock")
        self.report = mock.MagicMock()
        p = mock.patch.object(browse, "ADOPT_GRACE", 0)
        p.start()
        self.addCleanup(p.stop)

    def test_free_socket_needs_no_adoption(self):
        with mock.patch.object(browse, "probe", return_value=False):
            self.assertIs(browse._adopt_socket(self.sock, self.report), True)

    def test_kill_refused_means_failure(self):
        with mock.patch.object(browse, "probe", return_value=True), \
             mock.patch.object(browse, "_kill_occupant", return_value=False):
            self.assertIs(browse._adopt_socket(self.sock, self.report), False)

    def test_socket_freed_after_kill(self):
        states = iter([True, True, False, False])
        with mock.patch.object(browse, "probe", side_effect=lambda _: next(states)), \
             mock.patch.object(browse, "_kill_occupant", return_value=True):
            self.assertIs(browse._adopt_socket(self.sock, self.report), True)

    def test_still_occupied_after_kill(self):
        with mock.patch.object(browse, "probe", return_value=True), \
             mock.patch.object(browse, "_kill_occupant", return_value=True), \
             mock.patch.object(browse, "SPAWN_TIMEOUT", 0):
            self.assertIs(browse._adopt_socket(self.sock, self.report), False)


class TestDaemonRun(unittest.TestCase):
    """daemon_run：普通模式撞上在跑的直接失败；服务模式尝试接管。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock = pathlib.Path(self.tmp.name) / "browse.sock"

    def test_busy_socket_fails_in_normal_mode(self):
        with mock.patch.object(browse, "probe", return_value=True), \
             mock.patch.object(browse, "reporter") as rep:
            self.assertEqual(browse.daemon_run(self.sock, 60.0), browse.EXIT_FAILED)
        self.assertIn("已经有 daemon 在跑", rep.return_value.err.call_args.args[0])

    def test_service_mode_reports_failed_adoption(self):
        with mock.patch.object(browse, "probe", return_value=True), \
             mock.patch.object(browse, "_adopt_socket", return_value=False), \
             mock.patch.object(browse, "reporter") as rep:
            self.assertEqual(browse.daemon_run(self.sock, 0.0), browse.EXIT_FAILED)
        self.assertIn("接管失败", rep.return_value.err.call_args.args[0])

    def test_pid_file_is_written_then_removed(self):
        seen = {}

        def serve(sock, idle):
            seen["pid"] = browse.pid_path(sock).read_text(encoding="utf-8")
            return True

        with mock.patch.object(browse, "probe", return_value=False), \
             mock.patch.object(browse, "_serve", serve), \
             mock.patch.object(asyncio, "run", lambda coro: coro):
            rc = browse.daemon_run(self.sock, 60.0)
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertEqual(seen["pid"], str(os.getpid()))
        self.assertFalse(browse.pid_path(self.sock).exists())


class TestBridgeStatusAndLog(unittest.TestCase):
    """`browse bridge status` / `log` 的分支。"""

    def _status(self, handler, *, service=True):
        with mock.patch("lib.browse_service.status", return_value={"installed": service}):
            return call(["bridge", "status"], handler)

    def test_status_reports_browsers_and_uptime(self):
        def handler(method, params):
            if method == browse.BROWSERS_METHOD:
                return {"browsers": ["chrome", "brave"]}
            return {"uptimeSeconds": 12, "pid": 9, "port": 9330, "logPath": "/tmp/l.log"}

        rc, _, err, _ = self._status(handler)
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertIn("连着的浏览器（2）：chrome, brave", err)
        self.assertIn("要加 --browser", err)
        self.assertIn("已跑 12 秒", err)
        self.assertIn("开机自启：已装", err)

    def test_status_without_browsers_says_so(self):
        def handler(method, params):
            if method == browse.BROWSERS_METHOD:
                return {"browsers": []}
            return {"uptimeSeconds": 1, "pid": 1, "port": 1, "logPath": "/tmp/l.log"}

        rc, _, err, _ = self._status(handler, service=False)
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertIn("没有浏览器连着", err)
        self.assertIn("开机自启：没装", err)

    def test_status_propagates_a_failed_query(self):
        failed = {"status": "failed", "error": browse.ERR_NOT_CONNECTED, "message": "没连上"}
        rc, _, err, _ = self._status(lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)
        self.assertEqual(error_json(err)["error"], browse.ERR_NOT_CONNECTED)

    def test_log_prints_one_json_per_line(self):
        rc, out, _, calls = call(["bridge", "log", "--limit", "2"],
                                 lambda m, p: {"lines": [{"a": 1}, {"b": 2}]})
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertEqual([json.loads(line) for line in out.splitlines()], [{"a": 1}, {"b": 2}])
        self.assertEqual(calls[0][1], {"limit": 2})

    def test_log_without_daemon_points_at_the_file(self):
        rc, _, err, _ = call(["bridge", "log"], daemon=False)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertIn("daemon 没在跑", err)
        self.assertIn("日志文件还在", err)

    def test_log_propagates_failure(self):
        rc, _, err, _ = call(["bridge", "log"],
                             lambda m, p: {"status": "failed", "error": "", "message": "炸了"})
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertEqual(json.loads(err)["message"], "炸了")


class TestTargetResolutionMore(unittest.TestCase):
    """选页链剩下的两级：--context 直通、--group 挑组内活动页。"""

    def test_context_flag_wins(self):
        _, _, _, calls = call(["click", "登录", "--context", "7"])
        self.assertEqual(calls[-1][1]["context"], "7")

    def test_group_resolves_to_the_active_tab(self):
        def handler(method, params):
            if method == "lg:tabs.groups":
                return groups(("9", "browse/调研", 1, ["1", "2"]))
            if method == "browsingContext.getTree":
                return tabs(("1", "https://a.com", False), ("2", "https://b.com", True))
            return {}

        _, _, _, calls = call(["click", "登录", "--group", "调研"], handler)
        self.assertEqual(calls[-1][1]["context"], "2")

    def test_single_member_group_needs_no_active_flag(self):
        def handler(method, params):
            if method == "lg:tabs.groups":
                return groups(("9", "browse/调研", 1, ["1"]))
            if method == "browsingContext.getTree":
                return tabs(("1", "https://a.com", False))
            return {}

        _, _, _, calls = call(["click", "登录", "--group", "调研"], handler)
        self.assertEqual(calls[-1][1]["context"], "1")

    def test_group_without_a_unique_active_tab_errors(self):
        def handler(method, params):
            if method == "lg:tabs.groups":
                return groups(("9", "browse/调研", 1, ["1", "2"]))
            if method == "browsingContext.getTree":
                return tabs(("1", "https://a.com", False), ("2", "https://b.com", False))
            return {}

        rc, _, err, _ = call(["click", "登录", "--group", "调研"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("没有唯一的活动页", err)

    def test_unknown_group_name_lists_nothing_found(self):
        rc, _, err, _ = call(["click", "x", "--group", "不存在"],
                             lambda m, p: groups())
        self.assertEqual(rc, 2)
        self.assertIn("没有叫 browse/不存在 的分组", err)

    def test_same_group_name_in_two_windows_is_ambiguous(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, ["1"]),
                                      ("10", "browse/调研", 2, ["2"]))
        rc, _, err, _ = call(["click", "x", "--group", "调研"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("各有一组", err)


class TestOpenAndClose(unittest.TestCase):
    """open 的分组分支与 close 的目标分支。"""

    def test_open_without_url_is_a_usage_error(self):
        rc, _, err, _ = call(["open"])
        self.assertEqual(rc, 2)
        self.assertIn("要给网址", err)

    def test_no_group_flag_ungroups_the_new_tab(self):
        """命令行的 `--no-group` 必须真的退出自动分组。

        split_tokens 把 `--no-x` 统一折成 opts['x']=False，所以这里判的是
        opts['group'] is False。
        """
        handler = lambda m, p: {"context": "42"} if m == "browsingContext.create" else {}
        rc, out, _, calls = call(["open", "https://a.com", "--no-group"], handler)
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in calls], ["browsingContext.create", "lg:tabs.ungroup"])
        self.assertIsNone(json.loads(out)["group"])

    def test_without_no_group_the_tab_joins_the_default_group(self):
        handler = lambda m, p: {"context": "42"} if m == "browsingContext.create" else {}
        _rc, _out, _err, calls = call(["open", "https://a.com"], handler)
        self.assertNotIn("lg:tabs.ungroup", [m for m, _ in calls])

    def test_duplicate_target_groups_block_the_open(self):
        def handler(method, params):
            if method == "browsingContext.create":
                return {"context": "42"}
            return groups(("9", "browse/default", 1, []), ("10", "browse/default", 2, []))

        rc, _, err, _ = call(["open", "https://a.com"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("分不清新页进哪个", err)

    def test_close_by_context_flag(self):
        rc, out, _, calls = call(["close", "--context", "7"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[-1], ("browsingContext.close", {"context": "7"}))
        self.assertEqual(json.loads(out)["closed"], 1)

    def test_close_whole_group(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, ["1", "2"]))
        rc, out, _, calls = call(["close", "--group", "调研"], handler)
        self.assertEqual(rc, 0)
        closed = [p["context"] for m, p in calls if m == "browsingContext.close"]
        self.assertEqual(closed, ["1", "2"])
        self.assertEqual(json.loads(out)["closed"], 2)

    def test_close_empty_group_errors(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, []))
        rc, _, err, _ = call(["close", "--group", "调研"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("已经没有标签页了", err)

    def test_close_without_target_closes_the_active_tab(self):
        rc, out, _, calls = call(["close"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[-1], ("browsingContext.close", {}))
        self.assertEqual(json.loads(out)["closed"], 1)

    def test_close_zero_match_lists_current_tabs(self):
        handler = lambda m, p: tabs(("1", "https://a.com/1", True))
        rc, _, err, _ = call(["close", "nope.com/*"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("一个都没匹配到", flat(err))
        self.assertIn("a.com/1", err)

    def test_close_reports_partial_failure(self):
        def handler(method, params):
            if method == "browsingContext.close":
                return {"status": "failed", "error": "", "message": "关不掉"}
            return {}

        rc, out, err, _ = call(["close", "--context", "7"], handler)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertIn("关不掉", json.loads(err.splitlines()[0])["message"])
        self.assertEqual(json.loads(out)["failed"], 1)


class TestListFilters(unittest.TestCase):
    """list 的两个过滤器与 Firefox 无分组能力时的退化。"""

    def handler(self, groups_outcome):
        def inner(method, params):
            if method == "browsingContext.getTree":
                return tabs(("1", "https://a.com", True), ("2", "https://b.com", False))
            return groups_outcome
        return inner

    def test_group_filter_keeps_only_members(self):
        rows = self.handler(groups(("9", "browse/调研", 1, ["2"])))
        rc, out, _, _ = call(["list", "--group", "调研"], rows)
        self.assertEqual(rc, 0)
        self.assertEqual([t["id"] for t in json.loads(out)["tabs"]], ["2"])

    def test_tree_failure_propagates(self):
        failed = {"status": "failed", "error": browse.ERR_NOT_CONNECTED, "message": "没连上"}
        rc, _, err, _ = call(["list"], lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)
        self.assertEqual(error_json(err)["message"], "没连上")

    def test_groups_failure_still_lists_tabs(self):
        failed = {"status": "failed", "error": "", "message": "没有分组能力"}
        rc, out, _, _ = call(["list"], self.handler(failed))
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)["tabs"]), 2)


class TestScreenshot(unittest.TestCase):
    """screenshot：--base64 直出、默认落 ~/Downloads、jpeg 补后缀。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        (self.home / "Downloads").mkdir()

    def test_base64_flag_prints_the_payload(self):
        rc, out, _, _ = call(["screenshot", "--base64"], lambda m, p: {"data": "aGk="})
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"], "aGk=")

    def test_default_path_is_in_downloads(self):
        with mock.patch.object(pathlib.Path, "home", return_value=self.home):
            rc, out, _, _ = call(["screenshot"], lambda m, p: {"data": "aGk="})
        self.assertEqual(rc, 0)
        written = pathlib.Path(json.loads(out)["file"])
        self.assertEqual(written.parent, self.home / "Downloads")
        self.assertEqual(written.read_bytes(), b"hi")

    def test_jpeg_without_suffix_gets_jpg(self):
        target = self.home / "shot"
        rc, out, _, _ = call(["screenshot", str(target), "--format", "jpeg"],
                             lambda m, p: {"data": "aGk="})
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["file"], str(target.with_suffix(".jpg")))


class TestEvalWrappers(unittest.TestCase):
    """eval / text / back：一段写死的 JS 包一层 script.evaluate。"""

    def test_eval_prints_the_value(self):
        rc, out, _, _ = call(["eval", "1+1"], lambda m, p: {"result": {"value": 2}})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "2")

    def test_eval_string_value_is_raw(self):
        rc, out, _, _ = call(["eval", "x"], lambda m, p: {"result": {"value": "你好"}})
        self.assertEqual(out, "你好\n")

    def test_back_and_forward_run_the_hardcoded_js_and_print(self):
        for verb, js in (("back", "history.back()"), ("forward", "history.forward()")):
            with self.subTest(verb=verb):
                rc, out, _, calls = call([verb], lambda m, p: {"result": {"value": None}})
                self.assertEqual(rc, 0)
                self.assertEqual(calls[0][0], "script.evaluate")
                self.assertEqual(calls[0][1]["expression"], js)
                self.assertTrue(out.strip(), "结果要打出来，不能崩在打印那一步")


class TestSnapshotHit(unittest.TestCase):
    """_snapshot_hit：四种定位符的匹配规则。"""

    ENTRIES = [{"text": "提交订单", "css": "#submit", "xpath": "//button[1]"}]

    def test_text_prefix(self):
        self.assertTrue(browse._snapshot_hit(self.ENTRIES, "text=提交"))
        self.assertFalse(browse._snapshot_hit(self.ENTRIES, "text=取消"))

    def test_css_prefix(self):
        self.assertTrue(browse._snapshot_hit(self.ENTRIES, "css=#submit"))

    def test_xpath_prefix(self):
        self.assertTrue(browse._snapshot_hit(self.ENTRIES, "xpath=//button"))

    def test_js_prefix_is_refused(self):
        with self.assertRaises(browse.UsageError):
            browse._snapshot_hit(self.ENTRIES, "js=document.body")

    def test_bare_target_matches_text(self):
        self.assertTrue(browse._snapshot_hit(self.ENTRIES, "订单"))


class TestWaitMore(unittest.TestCase):
    """wait 的条件校验与失败路径。"""

    def test_no_condition_is_a_usage_error(self):
        rc, _, err, _ = call(["wait"])
        self.assertEqual(rc, 2)
        self.assertIn("正好给一个条件", err)

    def test_two_conditions_are_refused(self):
        rc, _, err, _ = call(["wait", "提交", "--text", "完成"])
        self.assertEqual(rc, 2)
        self.assertIn("正好给一个条件", err)

    def test_gone_value_plus_target_is_refused(self):
        # 这条只能直接调函数：route 会把 `--gone <值>` 折成位置参数，CLI 里走不到
        with self.assertRaises(browse.UsageError):
            asyncio.run(browse._cmd_wait({"gone": "x", "target": "y"}, {},
                                         pathlib.Path("/tmp/s.sock"), ""))

    def test_gone_value_becomes_the_target(self):
        # 同上，只能直接调：`--gone <值>` 在 route 那层就被折成位置参数了
        async def fake_execute(method, params, sock, duration=None, browser=""):
            return {"status": "ok", "result": {"elements": []}}

        with mock.patch.object(browse, "execute", fake_execute), \
             mock.patch("sys.stderr", io.StringIO()):
            rc = asyncio.run(browse._cmd_wait({"gone": "加载中"}, {"timeout": "10ms"},
                                              pathlib.Path("/tmp/s.sock"), ""))
        self.assertEqual(rc, browse.EXIT_OK)  # 元素本来就不在 = 已经消失

    def test_bare_idle_flag_delegates_to_the_idle_waiter(self):
        """帮助里写的就是裸 `--idle`：它在 split_tokens 那层是 True，必须认。"""
        with mock.patch.object(browse, "_cmd_wait_idle",
                               mock.AsyncMock(return_value=browse.EXIT_OK)) as idle:
            rc, _out, _err, _calls = call(["wait", "--idle", "--timeout", "2s"])
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertEqual(idle.call_args.args[2], 2.0)

    def test_idle_cannot_be_combined_with_another_condition(self):
        rc, _out, err, _calls = call(["wait", "--idle", "--text", "完成"])
        self.assertEqual(rc, 2)
        self.assertIn("正好给一个条件", flat(err))

    def test_url_mode_matches_the_active_tab(self):
        handler = lambda m, p: tabs(("1", "https://a.com/ok", True))
        rc, _, _, _ = call(["wait", "--url", "a.com/ok", "--timeout", "5s"], handler)
        self.assertEqual(rc, 0)

    def test_wait_passes_context_flag(self):
        def handler(method, params):
            return {"elements": [{"text": "提交"}]}

        _, _, _, calls = call(["wait", "提交", "--context", "7", "--timeout", "5s"], handler)
        self.assertEqual(calls[0][1]["context"], "7")

    def test_snapshot_failure_keeps_polling_until_timeout(self):
        def handler(method, params):
            if method == "lg:page.snapshot":
                return {"status": "failed", "error": "", "message": "页面没了"}
            return tabs(("1", "https://a.com", True))

        rc, _, err, _ = call(["wait", "提交", "--timeout", "10ms"], handler)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertIn("超过", flat(err))

    def test_text_mode_failure_keeps_polling(self):
        failed = {"status": "failed", "error": "", "message": "没页面"}
        rc, _, _, _ = call(["wait", "--text", "完成", "--timeout", "10ms"], lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_FAILED)


class TestWaitIdle(unittest.TestCase):
    """--idle：订一圈网络事件，连续安静就算通过。"""

    def _run(self, *, reply, frames=(), timeout=5.0, gap=0.01):
        writer = mock.MagicMock()
        writer.drain = mock.AsyncMock()
        reader = mock.MagicMock()
        stream = list(frames) or [{"type": "other"}]

        async def read_frame(_reader, _max):
            return stream[0]

        with mock.patch.object(browse, "connect",
                               mock.AsyncMock(return_value=(reader, writer, None))), \
             mock.patch.object(browse, "_await_reply", mock.AsyncMock(return_value=reply)), \
             mock.patch.object(browse, "read_frame", read_frame), \
             mock.patch.object(browse, "WAIT_IDLE_GAP", gap), \
             mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc = asyncio.run(browse._cmd_wait_idle(pathlib.Path("/tmp/s.sock"), "", timeout))
        return rc, writer

    def test_quiet_network_succeeds(self):
        rc, writer = self._run(reply={"type": "success", "result": {"subscription": "s1"}})
        self.assertEqual(rc, browse.EXIT_OK)
        writer.close.assert_called_once()

    def test_timeout_without_quiet_fails(self):
        rc, _ = self._run(reply={"type": "success", "result": {}}, timeout=0.0)
        self.assertEqual(rc, browse.EXIT_FAILED)

    def test_subscribe_error_propagates(self):
        rc, _ = self._run(reply={"type": "error", "error": browse.ERR_NOT_CONNECTED,
                                 "message": "没连上"})
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)

    def test_events_keep_resetting_the_quiet_timer(self):
        # 先来一个事件（计时重置），再断开 → 没安静成，超时失败
        frames = [{"type": "event"}, OSError("断了")]

        async def read_frame(_reader, _max):
            got = frames.pop(0)
            if isinstance(got, Exception):
                raise got
            return got

        writer = mock.MagicMock()
        writer.drain = mock.AsyncMock()
        with mock.patch.object(browse, "connect",
                               mock.AsyncMock(return_value=(mock.MagicMock(), writer, None))), \
             mock.patch.object(browse, "_await_reply",
                               mock.AsyncMock(return_value={"type": "success", "result": {}})), \
             mock.patch.object(browse, "read_frame", read_frame), \
             mock.patch.object(browse, "WAIT_IDLE_GAP", 10), \
             mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc = asyncio.run(browse._cmd_wait_idle(pathlib.Path("/tmp/s.sock"), "", 5.0))
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertEqual(frames, [])

    def test_read_timeout_just_keeps_waiting(self):
        async def read_frame(_reader, _max):
            await asyncio.sleep(1)  # 永远不返回 → 被 wait_for 掐成 TimeoutError

        writer = mock.MagicMock()
        writer.drain = mock.AsyncMock()
        with mock.patch.object(browse, "connect",
                               mock.AsyncMock(return_value=(mock.MagicMock(), writer, None))), \
             mock.patch.object(browse, "_await_reply",
                               mock.AsyncMock(return_value={"type": "success", "result": {}})), \
             mock.patch.object(browse, "read_frame", read_frame), \
             mock.patch.object(browse, "WAIT_IDLE_GAP", 0.01), \
             mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc = asyncio.run(browse._cmd_wait_idle(pathlib.Path("/tmp/s.sock"), "", 5.0))
        self.assertEqual(rc, browse.EXIT_OK)

    def test_connect_failure_is_not_connected(self):
        with mock.patch.object(browse, "connect", mock.AsyncMock(side_effect=OSError("no sock"))), \
             mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            rc = asyncio.run(browse._cmd_wait_idle(pathlib.Path("/tmp/s.sock"), "", 1.0))
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)


class TestGroupCommands(unittest.TestCase):
    """group 组的五条命令：list / add / rename / color / dissolve。"""

    def test_list_flattens_每组一行(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, ["1", "2"]))
        rc, out, _, _ = call(["group", "list"], handler)
        self.assertEqual(rc, 0)
        row = json.loads(out)["groups"][0]
        self.assertEqual((row["title"], row["tabs"], row["window"]), ("browse/调研", 2, 1))

    def test_add_without_name_errors(self):
        rc, _, err, _ = call(["group", "add"])
        self.assertEqual(rc, 2)
        self.assertIn("要给组名", err)

    def test_add_creates_with_hashed_color(self):
        rc, _, _, calls = call(["group", "add", "调研"], lambda m, p: groups())
        self.assertEqual(rc, 0)
        body = calls[-1][1]
        self.assertEqual(body["title"], "browse/调研")
        self.assertIn(body["color"], browse.GROUP_COLORS)

    def test_add_reuses_an_existing_group(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, ["1"]))
        _, _, _, calls = call(["group", "add", "调研"], handler)
        self.assertEqual(calls[-1][1]["group"], "9")

    def test_add_refuses_duplicate_groups(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, []), ("10", "browse/调研", 2, []))
        rc, _, err, _ = call(["group", "add", "调研"], handler)
        self.assertEqual(rc, 2)
        self.assertIn("分不清进哪个", err)

    def test_rename_needs_two_names(self):
        rc, _, err, _ = call(["group", "rename", "旧"])
        self.assertEqual(rc, 2)
        self.assertIn("要两个名字", err)

    def test_rename_maps_to_group_id(self):
        handler = lambda m, p: groups(("9", "browse/旧", 1, ["1"]))
        rc, _, _, calls = call(["group", "rename", "旧", "新"], handler)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[-1], ("lg:tabs.updateGroup", {"group": "9", "title": "browse/新"}))

    def test_color_needs_a_known_color(self):
        rc, _, err, _ = call(["group", "color", "调研", "赤"])
        self.assertEqual(rc, 2)
        self.assertIn("颜色只认", err)

    def test_color_needs_both_args(self):
        rc, _, err, _ = call(["group", "color", "调研"])
        self.assertEqual(rc, 2)
        self.assertIn("要名字和颜色", err)

    def test_color_updates_the_group(self):
        handler = lambda m, p: groups(("9", "browse/调研", 1, ["1"]))
        rc, _, _, calls = call(["group", "color", "调研", "red"], handler)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[-1], ("lg:tabs.updateGroup", {"group": "9", "color": "red"}))

    def test_dissolve_without_name_errors(self):
        rc, _, err, _ = call(["group", "dissolve"])
        self.assertEqual(rc, 2)
        self.assertIn("要给组名", err)


class TestNetWatchAndApi(unittest.TestCase):
    """net watch 的失败路径与 api 透传的失败路径。"""

    def test_net_watch_failure_propagates(self):
        failed = {"status": "failed", "error": browse.ERR_USER_REJECTED, "message": "拒了"}
        rc, _, err, _ = call(["net", "watch", "--match-url", "*/api/*"], lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_REJECTED)
        self.assertEqual(json.loads(err)["error"], browse.ERR_USER_REJECTED)

    def test_duration_outside_net_watch_is_refused(self):
        rc, _, err, _ = call(["list", "--duration", "30s"])
        self.assertEqual(rc, 2)
        self.assertIn("--duration 只对", err)

    def test_daemon_down_is_exit_3(self):
        rc, _, err, _ = call(["list"], daemon=False)
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)
        self.assertEqual(error_json(err)["error"], browse.ERR_NOT_CONNECTED)


class TestAuditMore(unittest.TestCase):
    """audit：clear、--table、daemon 没起来。"""

    def test_clear_reports_the_count(self):
        rc, _, err, calls = call(["audit", "clear"], lambda m, p: {"cleared": 12})
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][0], "lg:audit.clear")
        self.assertIn("已清空 12 条审计", err)

    def test_clear_failure_propagates(self):
        failed = {"status": "failed", "error": "", "message": "清不掉"}
        rc, _, err, _ = call(["audit", "clear"], lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertEqual(json.loads(err)["message"], "清不掉")

    def test_unknown_subcommand_errors(self):
        rc, _, err, _ = call(["audit", "夸张"])
        self.assertEqual(rc, 2)
        self.assertIn("audit 只有", err)

    def test_table_flag_switches_format(self):
        entries = [{"at": 1, "method": "input.click"}]
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            rc, out, _, _ = call(["audit", "--table"], lambda m, p: {"entries": entries})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "at\tmethod\n1\tinput.click\n")

    def test_daemon_down_is_exit_3(self):
        rc, _, err, _ = call(["audit"], daemon=False)
        self.assertEqual(rc, browse.EXIT_NOT_CONNECTED)
        self.assertIn("daemon 起不来", json.loads(err)["message"])


class TestStopCommand(unittest.TestCase):
    """`browse stop`：中止在途指令，daemon 留着。"""

    def test_reports_aborted_count(self):
        rc, _, err, calls = call(["stop"], lambda m, p: {"aborted": 3})
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][0], browse.ABORT_METHOD)
        self.assertIn("已中止 3 条", err)

    def test_failure_propagates(self):
        failed = {"status": "failed", "error": "", "message": "停不掉"}
        rc, _, err, _ = call(["stop"], lambda m, p: failed)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertEqual(json.loads(err)["message"], "停不掉")

    def test_no_daemon_is_still_success(self):
        rc, _, err, _ = call(["stop"], daemon=False)
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertIn("没有在途指令", flat(err))


class TestStatusCommand(unittest.TestCase):
    """`browse status`：bridge / 插件连接 / 旧注册三段。"""

    def _status(self, *, running=True, conns=(), leftovers=()):
        rows = [{"browser": b, "manifests": [{"exists": True}]} for b in leftovers]

        def handler(method, params):
            return {"connections": list(conns)}

        with mock.patch("lib.browse_install.install_status", return_value=rows), \
             mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path("/tmp")):
            return call(["status"], handler, daemon=running)

    def test_connected_extension_is_exit_0(self):
        conn = {"browser": "chrome", "connectionId": "c1", "sinceSeconds": 10, "idleSeconds": 1}
        rc, _, err, _ = self._status(conns=[conn])
        self.assertEqual(rc, browse.EXIT_OK)
        self.assertIn("chrome: 插件已连接", err)

    def test_running_without_connections_is_exit_1(self):
        rc, _, err, _ = self._status()
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertIn("没有任何插件连着", err)

    def test_bridge_down_says_it_will_autostart(self):
        rc, _, err, _ = self._status(running=False)
        self.assertEqual(rc, browse.EXIT_FAILED)
        self.assertIn("自动把它拉起来", err)

    def test_leftover_registrations_are_reported(self):
        rc, _, err, _ = self._status(leftovers=["chrome", "brave"])
        self.assertIn("旧 native messaging 注册还在", err)
        self.assertIn("brave, chrome", err)


class TestInstallDispatch(unittest.TestCase):
    """`browse install` / `uninstall` 转给 lib.browse_install.main。"""

    def test_install_forwards_flags(self):
        with mock.patch("lib.browse_install.main", return_value=0) as install:
            rc = browse._main(["browse", "install", "--no-wait"])
        self.assertEqual(rc, 0)
        self.assertEqual(install.call_args.args[0], ["browse install", "--no-wait"])

    def test_uninstall_adds_the_flag(self):
        with mock.patch("lib.browse_install.main", return_value=0) as install:
            browse._main(["browse", "uninstall"])
        self.assertEqual(install.call_args.args[0], ["browse install", "--uninstall"])


class TestRunnable(unittest.TestCase):
    """`run` 只吃一步能发出去的命令。"""

    def test_noun_group_command_is_runnable(self):
        self.assertEqual(browse._runnable("data cookies", {"domain": "a.com"}),
                         ("storage.getCookies", {"domain": "a.com"}))

    def test_multi_step_command_is_refused(self):
        with self.assertRaises(browse.UsageError):
            browse._runnable("open", {"url": "https://a.com"})


if __name__ == "__main__":
    unittest.main()
