"""browse CLI 测试：参数解析、fail-fast 三态、并发上限、退出码、stdin、输出分流。

跑得起来的都用真 daemon + 假浏览器（一条 `role="native-host"` 的连接），不 mock
传输层——退出码这种东西只有端到端跑一遍才算数。
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_daemon import (  # noqa: E402
    Daemon,
    connect,
    pack,
    read_frame,
)
from lib.browse_protocol import (  # noqa: E402
    ERR_INVALID_ARGUMENT,
    ERR_NOT_CONNECTED,
    ERR_USER_REJECTED,
    MAX_INCOMING_FRAME_BYTES,
    error,
    success,
)
from lib.cli import browse  # noqa: E402

TIMEOUT = 5.0


class FakeBrowser:
    """假浏览器：按 handler 的返回值回包。

    handler(method, params) 返回 dict 当 result（成功），返回 (code, message) 当错误。
    """

    def __init__(self, handler):
        self.handler = handler
        self.inflight = 0
        self.peak = 0
        self.seen: list[str] = []

    async def pump(self, sock: pathlib.Path) -> None:
        reader, writer, _ = await connect("native-host", sock)
        self.writer = writer
        while True:
            try:
                message = await read_frame(reader, MAX_INCOMING_FRAME_BYTES)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            asyncio.ensure_future(self._answer(writer, message))

    async def _answer(self, writer, message: dict) -> None:
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.seen.append(message["method"])
        try:
            outcome = self.handler(message["method"], message.get("params", {}))
            if asyncio.iscoroutine(outcome):
                outcome = await outcome
            reply = (error(message["id"], *outcome) if isinstance(outcome, tuple)
                     else success(message["id"], outcome))
            writer.write(pack(reply, MAX_INCOMING_FRAME_BYTES))
            await writer.drain()
        finally:
            self.inflight -= 1


class Harness:
    """一个临时目录里的真 daemon，跑在后台线程的事件循环上。"""

    def __init__(self, handler=None):
        self.handler = handler
        self.browser: FakeBrowser | None = None

    def __enter__(self) -> Harness:
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.sock = self.dir / "browse.sock"
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self._call(self._start())
        return self

    def __exit__(self, *exc) -> None:
        self._call(self._shutdown())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(TIMEOUT)
        self.loop.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(TIMEOUT)

    async def _start(self) -> None:
        # daemon 现在是纯管道：策略和审计都在插件里，这边没有配置也没有落盘
        self.daemon = Daemon(path=self.sock, idle_timeout=600.0)
        await self.daemon.start()
        if self.handler is None:
            return
        self.browser = FakeBrowser(self.handler)
        self._pump = asyncio.ensure_future(self.browser.pump(self.sock))
        await asyncio.sleep(0.05)

    async def _shutdown(self) -> None:
        """先断假浏览器，再停 daemon —— `wait_closed()` 要等所有连接处理完。"""
        if self.browser is not None:
            self._pump.cancel()
            self.browser.writer.close()
        await self.daemon.stop()

    def cli(self, *argv: str) -> int:
        """跑一次 `browse ...`，socket 指向本 harness。"""
        return browse._main(["browse", *argv, "--socket", str(self.sock)])


# ---------------------------------------------------------------- 参数解析
class TestParsing(unittest.TestCase):
    def test_camel(self):
        self.assertEqual(browse._camel("match-url"), "matchUrl")
        self.assertEqual(browse._camel("url"), "url")
        self.assertEqual(browse._camel("a-b-c"), "aBC")

    def test_coerce_json_else_string(self):
        self.assertEqual(browse._coerce("3"), 3)
        self.assertEqual(browse._coerce("true"), True)
        self.assertEqual(browse._coerce("null"), None)
        self.assertEqual(browse._coerce('["xhr"]'), ["xhr"])
        self.assertEqual(browse._coerce("example.com"), "example.com")
        self.assertEqual(browse._coerce('"123"'), "123")

    def test_split_tokens_forms(self):
        pos, flags = browse.split_tokens(
            ["a", "--index", "2", "--all", "--no-wait", "--match-url=*/api/*", "--", "--b"])
        self.assertEqual(pos, ["a", "--b"])
        self.assertEqual(flags, {"index": 2, "all": True, "wait": False, "matchUrl": "*/api/*"})

    def test_split_tokens_negative_number_is_a_value(self):
        _, flags = browse.split_tokens(["--dy", "-100"])
        self.assertEqual(flags, {"dy": -100})

    def test_split_tokens_rejects_empty_name(self):
        self.assertEqual(browse.split_tokens(["--"]), ([], {}))  # 光秃秃的 -- 合法
        with self.assertRaises(browse.UsageError):
            browse.split_tokens(["--=v"])

    def test_resolve_method_adds_lg_prefix(self):
        self.assertEqual(browse.resolve_method("history", "search"), "lg:history.search")
        self.assertEqual(browse.resolve_method("lg:history", "search"), "lg:history.search")
        self.assertEqual(browse.resolve_method("input", "click"), "input.click")
        with self.assertRaises(browse.UsageError):
            browse.resolve_method("input", "teleport")

    def test_parse_command_positional_and_flags(self):
        method, params, opts = browse.parse_command(
            ["input", "type", "css=input[name=user]", "myname", "--clear", "--table"])
        self.assertEqual(method, "input.type")
        self.assertEqual(params, {"selector": "css=input[name=user]", "text": "myname", "clear": True})
        self.assertEqual(opts, {"table": True})

    def test_parse_command_rejects_extra_positional(self):
        with self.assertRaises(browse.UsageError):
            browse.parse_command(["input", "click", "css=a", "css=b"])

    def test_parse_command_needs_module_and_action(self):
        with self.assertRaises(browse.UsageError):
            browse.parse_command(["input"])

    def test_parse_run_item_shell_style(self):
        method, params = browse.parse_run_item(
            "browsingContext.navigate https://a.com --wait none")
        self.assertEqual(method, "browsingContext.navigate")
        self.assertEqual(params, {"url": "https://a.com", "wait": "none"})

    def test_parse_run_item_quoted_selector_keeps_spaces(self):
        method, params = browse.parse_run_item("input.click 'text=登 录'")
        self.assertEqual((method, params), ("input.click", {"selector": "text=登 录"}))

    def test_parse_run_item_rejects_bare_method(self):
        with self.assertRaises(browse.UsageError):
            browse.parse_run_item("navigate https://a.com")
        with self.assertRaises(browse.UsageError):
            browse.parse_run_item("   ")

    def test_parse_run_item_rejects_cli_flags(self):
        with self.assertRaises(browse.UsageError):
            browse.parse_run_item("input.click css=a --table")

    def test_parse_duration(self):
        self.assertEqual(browse.parse_duration("30s"), 30.0)
        self.assertEqual(browse.parse_duration("2m"), 120.0)
        self.assertEqual(browse.parse_duration("500ms"), 0.5)
        self.assertEqual(browse.parse_duration("1h"), 3600.0)
        self.assertEqual(browse.parse_duration(1.5), 1.5)
        with self.assertRaises(browse.UsageError):
            browse.parse_duration("一会儿")

    def test_read_stdin_items_skips_blanks_and_comments(self):
        text = "input.click css=a\n\n# 注释\n  script.evaluate 1+1  \n"
        self.assertEqual(browse.read_stdin_items(text),
                         ["input.click css=a", "script.evaluate 1+1"])

    def test_methods_table_matches_extension_handlers(self):
        """CLI 的指令表必须和扩展侧 HANDLERS 逐条对齐，少一条就是 CLI 发不出去。

        2026-09-14 之前这里要排除两条 daemon 反过来问插件的控制消息
        （`lg:confirm.request` / `lg:context.url`）。裁决搬进插件之后 daemon 不再问
        任何问题，两条都没了，所以现在是**严格相等**，一个例外都不留。
        """
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "browser-extension/extension/src/handlers/index.ts").read_text(encoding="utf-8")
        import re
        handlers = set(re.findall(r'^\s+"([\w:]+\.\w+)":', source, re.M))
        self.assertEqual(handlers, set(browse.METHODS))

    def test_the_daemons_old_reverse_rpc_methods_are_gone(self):
        """daemon 不再问插件任何事，所以这几条方法名一个都不该还能发出去。

        留着的话就等于「谁都能弹一个假确认框 / 改别人的确认策略」。
        """
        for method in ("lg:confirm.request", "lg:context.url", "lg:approvals.list",
                       "lg:approvals.approve", "lg:approvals.revoke",
                       "lg:config.get", "lg:config.set"):
            self.assertNotIn(method, browse.METHODS)
            module, _, action = method.rpartition(".")
            with self.assertRaises(browse.UsageError, msg=method):
                browse.resolve_method(module, action)


# ---------------------------------------------------------------- 输出与退出码
class TestOutput(unittest.TestCase):
    def test_result_is_valid_json_on_stdout(self):
        buf = io.StringIO()
        browse.print_result({"contexts": [{"context": "1"}]}, table=False, out=buf)
        self.assertEqual(json.loads(buf.getvalue()), {"contexts": [{"context": "1"}]})

    def test_table_renders_rows(self):
        buf = io.StringIO()
        browse.print_result({"contexts": [{"context": "1", "url": "https://a.com"}]},
                            table=True, out=buf)
        self.assertIn("context", buf.getvalue())
        self.assertIn("https://a.com", buf.getvalue())

    def test_table_falls_back_to_key_value(self):
        buf = io.StringIO()
        browse.print_result({"deleted": 3}, table=True, out=buf)
        self.assertIn("deleted", buf.getvalue())

    def test_error_goes_to_stderr_in_spec_shape(self):
        buf = io.StringIO()
        browse.print_error({"error": ERR_INVALID_ARGUMENT, "message": "坏参数"}, err=buf)
        self.assertEqual(json.loads(buf.getvalue()),
                         {"type": "error", "error": ERR_INVALID_ARGUMENT, "message": "坏参数"})

    def test_exit_code_mapping(self):
        self.assertEqual(browse.exit_code_for({"status": "ok"}), 0)
        self.assertEqual(browse.exit_code_for({"status": "failed", "error": "timeout"}), 1)
        self.assertEqual(browse.exit_code_for({"status": "failed", "error": ERR_NOT_CONNECTED}), 3)
        self.assertEqual(browse.exit_code_for({"status": "failed", "error": ERR_USER_REJECTED}), 4)


# ---------------------------------------------------------------- 端到端
class TestSingleCommand(unittest.TestCase):
    def test_ok_prints_json_to_stdout_and_exits_0(self):
        with Harness(lambda method, params: {"contexts": [{"context": "7", "url": params}]}) as h:
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = h.cli("browsingContext", "getTree")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["contexts"][0]["context"], "7")
            self.assertEqual(h.browser.seen, ["browsingContext.getTree"])

    def test_positional_and_flags_reach_the_browser(self):
        got = {}

        def handler(method, params):
            got.update(params)
            return {}

        with Harness(handler) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                h.cli("input", "type", "css=input[name=user]", "myname", "--index", "2")
        self.assertEqual(got, {"selector": "css=input[name=user]", "text": "myname", "index": 2})

    def test_failed_command_exits_1_and_writes_only_stderr(self):
        with Harness(lambda m, p: ("no such element", "找不到 css=nope")) as h:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                code = h.cli("input", "click", "css=nope")
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue())["error"], "no such element")

    def test_browser_not_connected_exits_3(self):
        with Harness() as h:  # daemon 起着，但没有浏览器连上来
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code = h.cli("browsingContext", "getTree")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(err.getvalue())["error"], ERR_NOT_CONNECTED)

    def test_user_rejected_exits_4(self):
        with Harness(lambda m, p: (ERR_USER_REJECTED, "用户点了取消")) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("storage", "getCookies", "--domain", "example.com")
        self.assertEqual(code, 4)

    def test_unknown_command_exits_2_without_sending(self):
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("input", "teleport", "css=a")
        self.assertEqual(code, 2)
        self.assertEqual(h.browser.seen, [])

    def test_duration_only_for_subscribe(self):
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("input", "click", "css=a", "--duration", "5s")
        self.assertEqual(code, 2)
        self.assertEqual(h.browser.seen, [])

    def test_lg_prefix_is_added(self):
        with Harness(lambda m, p: {"nodes": []}) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                h.cli("bookmarks", "search", "python")
        self.assertEqual(h.browser.seen, ["lg:bookmarks.search"])

    def test_daemon_not_running_exits_3(self):
        """socket 指向一个不存在的路径且禁止自举 → 报浏览器未连接，不是崩掉。"""
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        err = io.StringIO()
        with mock.patch.object(browse, "ensure_daemon", return_value=False), \
             mock.patch("sys.stderr", err):
            code = browse._main(["browse", "browsingContext", "getTree", "--socket", str(missing)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(err.getvalue())["error"], ERR_NOT_CONNECTED)


class TestSubscribeStream(unittest.TestCase):
    def test_events_stream_as_jsonl_then_unsubscribe_with_daemon_id(self):
        """订阅 id 用 daemon 发的 `sub-N`，退订时原样回传，不碰扩展的内部 id。"""
        unsubscribed = {}

        def handler(method, params):
            if method == "network.subscribe":
                return {"subscription": "net-1", "lg:metadataOnly": True}
            unsubscribed.update(params)
            return {"removed": ["net-1"]}

        with Harness(handler) as h:
            async def push_event():
                await asyncio.sleep(0.05)
                h.browser.writer.write(pack({
                    "type": "event", "method": "network.responseCompleted",
                    "params": {"subscriptions": ["net-1"], "url": "https://a.com/api"},
                }, MAX_INCOMING_FRAME_BYTES))
                await h.browser.writer.drain()

            asyncio.run_coroutine_threadsafe(push_event(), h.loop)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = h.cli("network", "subscribe", "--match-url", "*/api/*", "--duration", "300ms")

        self.assertEqual(code, 0)
        # 事件是 JSONL（一行一条），最后那个订阅结果是缩进过的多行 JSON
        events = [json.loads(line) for line in out.getvalue().splitlines()
                  if line.startswith('{"type": "event"')]
        self.assertEqual(len(events), 1)
        # daemon 把扩展的 net-1 换成了对外的 sub-1
        self.assertEqual(events[0]["params"]["subscriptions"], ["sub-1"])
        # 退订时回传的也是 sub-1，daemon 再翻译回 net-1
        self.assertEqual(unsubscribed, {"subscription": "net-1"})


class TestRunBatch(unittest.TestCase):
    def _items(self, n: int) -> list[str]:
        return [f"browsingContext.navigate https://{i}.com" for i in range(n)]

    def test_all_ok_exits_0(self):
        with Harness(lambda m, p: {"url": p["url"]}) as h:
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = h.cli("run", *self._items(3))
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in report], ["ok"] * 3)
        self.assertEqual([r["index"] for r in report], [0, 1, 2])

    def test_fail_fast_marks_later_items_skipped_and_exits_nonzero(self):
        def handler(method, params):
            return ("timeout", "第 1 条超时") if params["url"] == "https://1.com" else {}

        with Harness(handler) as h:
            out = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("run", "--concurrency", "1", *self._items(5))
        report = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in report],
                         ["ok", "failed", "skipped", "skipped", "skipped"])
        self.assertEqual(report[1]["error"], "timeout")
        # 副作用不隐藏：失败前真的跑过的那条留着 ok
        self.assertEqual(h.browser.seen, ["browsingContext.navigate"] * 2)

    def test_fail_fast_exit_code_follows_the_error(self):
        with Harness(lambda m, p: (ERR_USER_REJECTED, "拒绝")) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                code = h.cli("run", "--concurrency", "1", *self._items(2))
        self.assertEqual(code, 4)

    def test_no_fail_fast_runs_everything_and_exits_0(self):
        def handler(method, params):
            return ("timeout", "超时") if params["url"] == "https://1.com" else {}

        with Harness(handler) as h:
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = h.cli("run", "--no-fail-fast", "--concurrency", "1", *self._items(4))
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual([r["status"] for r in report], ["ok", "failed", "ok", "ok"])
        self.assertEqual(len(h.browser.seen), 4)

    def test_concurrency_cap_is_respected(self):
        async def handler(method, params):
            await asyncio.sleep(0.1)
            return {}

        with Harness(handler) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                code = h.cli("run", "--concurrency", "3", *self._items(9))
        self.assertEqual(code, 0)
        self.assertLessEqual(h.browser.peak, 3)
        self.assertGreater(h.browser.peak, 1)

    def test_default_concurrency_is_four(self):
        async def handler(method, params):
            await asyncio.sleep(0.1)
            return {}

        with Harness(handler) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                h.cli("run", *self._items(12))
        self.assertLessEqual(h.browser.peak, browse.DEFAULT_CONCURRENCY)

    def test_stdin_mode(self):
        with Harness(lambda m, p: {}) as h:
            out = io.StringIO()
            stdin = io.StringIO("browsingContext.navigate https://a.com\n# 注释\n\ninput.click css=b\n")
            with mock.patch("sys.stdout", out), mock.patch("sys.stdin", stdin):
                code = h.cli("run", "-")
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual([r["command"] for r in report],
                         ["browsingContext.navigate https://a.com", "input.click css=b"])

    def test_bad_item_exits_2_before_anything_is_sent(self):
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("run", "browsingContext.navigate https://a.com", "input.teleport css=a")
        self.assertEqual(code, 2)
        self.assertEqual(h.browser.seen, [])

    def test_empty_run_exits_2(self):
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(h.cli("run"), 2)

    def test_bad_concurrency_exits_2(self):
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(h.cli("run", "--concurrency", "0", *self._items(1)), 2)

    def test_daemon_down_during_batch_exits_3(self):
        with mock.patch.object(browse, "ensure_daemon", return_value=False), \
             mock.patch("sys.stderr", io.StringIO()):
            code = browse._main(["browse", "run", "input.click css=a", "--socket", "/nope/x.sock"])
        self.assertEqual(code, 3)


class TestExecuteTransport(unittest.TestCase):
    """`execute` 自己那两条传输层失败路径。"""

    def test_socket_missing_is_reported_as_not_connected(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        outcome = asyncio.run(browse.execute("input.click", {"selector": "css=a"}, missing))
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], ERR_NOT_CONNECTED)

    def test_connection_dropped_mid_command_is_not_connected(self):
        """扩展重连那一瞬间，在途指令会以断连收场 —— 要接得住，不能崩。"""
        with Harness(lambda m, p: {}) as h:
            async def boom(*args, **kwargs):
                raise ConnectionResetError("对端跑了")

            with mock.patch.object(browse, "read_frame", boom):
                outcome = asyncio.run(browse.execute("input.click", {}, h.sock))
        self.assertEqual(outcome["error"], ERR_NOT_CONNECTED)

    def test_zero_duration_sends_unsubscribe_right_away(self):
        """`--duration 0s`：一条事件都不等，直接退订。"""
        with Harness(lambda m, p: {"subscription": "net-1"} if m == "network.subscribe"
                     else {"removed": ["net-1"]}) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                outcome = asyncio.run(browse.execute("network.subscribe", {}, h.sock, duration=0.0))
        self.assertEqual(outcome["status"], "ok")
        self.assertEqual(h.browser.seen, ["network.subscribe", "network.unsubscribe"])

    def test_subscribe_without_a_subscription_id_skips_unsubscribe(self):
        """订阅回包里没有 id 就不发退订 —— 不自己编一个 id 出来。"""
        with Harness(lambda m, p: {}) as h:
            with mock.patch("sys.stdout", io.StringIO()):
                outcome = asyncio.run(browse.execute(
                    "network.subscribe", {}, h.sock, duration=0.05))
        self.assertEqual(outcome["status"], "ok")
        self.assertEqual(h.browser.seen, ["network.subscribe"])


class TestRunBatchUnit(unittest.TestCase):
    """`run_batch` 里不好从 CLI 层触发的那条分支：在途指令被取消。"""

    def test_inflight_items_are_failed_not_skipped(self):
        async def scenario():
            calls = []

            async def fake_execute(method, params, sock, **_):
                calls.append(method)
                if params["n"] == 0:
                    await asyncio.sleep(0.05)  # 让第 1 条先进入在途
                    return {"status": "failed", "error": "timeout", "message": "x"}
                await asyncio.sleep(5)  # 会被 fail-fast 取消
                return {"status": "ok", "result": {}}

            items = [("input.click", {"n": i}) for i in range(4)]
            with mock.patch.object(browse, "execute", fake_execute):
                return await browse.run_batch(items, pathlib.Path("/nope"),
                                              concurrency=2, fail_fast=True)

        outcomes = asyncio.run(scenario())
        self.assertEqual(outcomes[0]["status"], "failed")
        self.assertEqual(outcomes[1]["status"], "failed")
        self.assertEqual(outcomes[1]["error"], "lg:cancelled")
        self.assertEqual([o["status"] for o in outcomes[2:]], ["skipped", "skipped"])


# ---------------------------------------------------------------- daemon 子命令
class TestDaemonCommands(unittest.TestCase):
    def test_status_and_start_are_idempotent(self):
        with Harness() as h:
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                self.assertEqual(h.cli("daemon", "status"), 0)
                self.assertEqual(h.cli("daemon", "start"), 0)
            self.assertIn("在跑", err.getvalue())

    def test_status_without_daemon_exits_1(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        with mock.patch("sys.stderr", io.StringIO()):
            code = browse._main(["browse", "daemon", "status", "--socket", str(missing)])
        self.assertEqual(code, 1)

    def test_unknown_daemon_action_exits_2(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(browse._main(["browse", "daemon", "restart"]), 2)

    def test_stop_when_not_running_is_ok(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(browse.daemon_stop(missing), 0)

    def test_stop_without_pid_file_reports_instead_of_guessing(self):
        with Harness() as h:
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code = browse.daemon_stop(h.sock)
        self.assertEqual(code, 1)
        self.assertIn("pid", err.getvalue())

    def test_stop_cleans_up_a_dead_pid(self):
        with Harness() as h:
            browse.pid_path(h.sock).write_text("2147483646", encoding="utf-8")
            with mock.patch("sys.stderr", io.StringIO()), \
                 mock.patch("os.kill", side_effect=ProcessLookupError):
                code = browse.daemon_stop(h.sock)
        self.assertEqual(code, 0)

    def test_stop_reports_when_sigterm_does_not_land(self):
        with Harness() as h:
            browse.pid_path(h.sock).write_text("2147483646", encoding="utf-8")
            err = io.StringIO()
            with mock.patch("sys.stderr", err), mock.patch("os.kill"), \
                 mock.patch.object(browse, "SPAWN_TIMEOUT", 0.05):
                code = browse.daemon_stop(h.sock)
        self.assertEqual(code, 1)
        self.assertIn("没退", err.getvalue())

    def test_browse_stop_aborts_inflight_and_leaves_the_daemon_running(self):
        """spec 4.5：`browse stop` 掐在途指令，daemon 留着 —— 不是 `daemon stop` 的别名。"""
        held = asyncio.Event()

        async def handler(method, params):
            await held.wait()  # 一直不回，让这条指令留在途中
            return {}

        with Harness(handler) as h:
            done: list[int] = []
            thread = threading.Thread(
                target=lambda: done.append(h.cli("storage", "getCookies",
                                                 "--domain", "bank.test")),
                daemon=True)
            with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                thread.start()
                for _ in range(100):
                    if h.browser and h.browser.inflight:
                        break
                    time.sleep(0.02)
                err = io.StringIO()
                with mock.patch("sys.stderr", err):
                    self.assertEqual(h.cli("stop"), 0)
                thread.join(TIMEOUT)
            self.assertIn("已中止 1 条在途指令", err.getvalue())
            self.assertEqual(done, [browse.EXIT_FAILED])
            self.assertTrue(browse.probe(h.sock), "daemon 必须还活着")
            h.loop.call_soon_threadsafe(held.set)

    def test_browse_stop_without_a_daemon_is_quiet(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(browse._main(["browse", "stop", "--socket", str(missing)]), 0)
        self.assertIn("没在跑", err.getvalue())

    def test_daemon_run_refuses_when_one_is_already_up(self):
        """返回值不能吞：已经有一个在跑时这次没起来，就是失败。"""
        with Harness() as h:
            browse.pid_path(h.sock).write_text("2147483646", encoding="utf-8")
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(browse.daemon_run(h.sock, 1.0), browse.EXIT_FAILED)
            # 而且没把别人的 pid 文件删掉
            self.assertTrue(browse.pid_path(h.sock).exists())

    def test_daemon_run_then_daemon_stop_end_to_end(self):
        """`browse daemon run` 前台跑起来，另一头 `browse daemon stop` 把它收干净。

        daemon run 必须留在主线程（`add_signal_handler` 只认主线程），所以反过来：
        stop 走后台线程。
        """
        import time as _time

        tmp = pathlib.Path(tempfile.mkdtemp())
        sock = tmp / "browse.sock"
        stopped: list[int] = []

        def stop_later():
            for _ in range(100):
                if browse.probe(sock):
                    with mock.patch("sys.stderr", io.StringIO()):
                        stopped.append(browse._main(["browse", "daemon", "stop", "--socket", str(sock)]))
                    return
                _time.sleep(0.05)

        thread = threading.Thread(target=stop_later, daemon=True)
        thread.start()
        self.assertEqual(browse._main(["browse", "daemon", "run", "--socket", str(sock)]), 0)
        thread.join(TIMEOUT)
        self.assertEqual(stopped, [0])
        self.assertFalse(sock.exists())
        self.assertFalse(browse.pid_path(sock).exists())
        shutil.rmtree(tmp, ignore_errors=True)

    def test_start_reports_failure_when_the_daemon_never_comes_up(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        err = io.StringIO()
        with mock.patch.object(browse, "ensure_daemon", return_value=False), \
             mock.patch("sys.stderr", err):
            code = browse._main(["browse", "daemon", "start", "--socket", str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("起不来", err.getvalue())


# ---------------------------------------------------------------- 入口
class TestEntry(unittest.TestCase):
    def test_help_works_without_a_daemon(self):
        for argv in (["browse"], ["browse", "--help"], ["browse", "-h"], ["browse", "help"]):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                self.assertEqual(browse._main(argv), 0)
            self.assertIn("browse <module> <action>", err.getvalue())

    def test_help_lists_every_method(self):
        text = browse.help_text()
        for method in browse.METHODS:
            module, _, action = method.rpartition(".")
            self.assertIn(f"browse {module} {action}", text)

    def test_main_consumes_repo_wide_flags(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(browse.main(["browse", "--help", "--debug", "--no-say"]), 0)

    def test_skills_flag_prints_ai_help_and_exits_zero(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), self.assertRaises(SystemExit) as ctx:
            browse.main(["browse", "--skills"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("受众：使用本命令的 AI agent。", out.getvalue())

    def test_dry_run_flag_exits_zero(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), self.assertRaises(SystemExit) as ctx:
            browse.main(["browse", "--dry-run"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("dry-run", out.getvalue())

    def test_native_host_is_dispatched_to_t03(self):
        fake = mock.Mock(return_value=7)
        module = type(sys)("lib.browse_native_host")
        module.main = fake
        with mock.patch.dict(sys.modules, {"lib.browse_native_host": module}):
            self.assertEqual(browse.main(["browse", "--native-host"]), 7)
        fake.assert_called_once_with(["browse", "--native-host"])


class TestEnsureDaemon(unittest.TestCase):
    def test_existing_daemon_is_reused_without_spawning(self):
        with Harness() as h:
            with mock.patch.object(browse.subprocess, "Popen") as popen:
                self.assertTrue(browse.ensure_daemon(h.sock))
            popen.assert_not_called()

    def test_spawn_then_ready_returns_true(self):
        """自举出来的 daemon 一就绪就返回，不空等满 SPAWN_TIMEOUT。"""
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        # 第一次 probe 没有，Popen 之后第二次就有了
        with mock.patch.object(browse.subprocess, "Popen"), \
             mock.patch.object(browse, "probe", side_effect=[False, True]), \
             mock.patch.object(browse.SCRIPT_PATH.__class__, "is_file", return_value=True):
            self.assertTrue(browse.ensure_daemon(missing))

    def test_no_spawn_flag_reports_missing(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        self.assertFalse(browse.ensure_daemon(missing, spawn=False))

    def test_spawn_launches_the_entry_script_detached(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        with mock.patch.object(browse.subprocess, "Popen") as popen, \
             mock.patch.object(browse, "SPAWN_TIMEOUT", 0.05), \
             mock.patch.object(browse.SCRIPT_PATH.__class__, "is_file", return_value=True):
            self.assertFalse(browse.ensure_daemon(missing))
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1:], [str(browse.SCRIPT_PATH), "daemon", "run", "--socket", str(missing)])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_missing_entry_script_is_a_usage_error(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "nope.sock"
        with mock.patch.object(browse.SCRIPT_PATH.__class__, "is_file", return_value=False):
            with self.assertRaises(browse.UsageError):
                browse.ensure_daemon(missing)


class TestAudit(unittest.TestCase):
    """`browse audit`：审计存在插件里，这是唯一能把它捞出来的路。"""

    def test_audit_prints_jsonl_one_entry_per_line(self):
        entries = [
            {"ts": "2026-09-14T20:00:00+08:00", "method": "storage.getCookies",
             "domain": "a.test", "action": "readCookies", "result": "success", "ms": 3},
            {"ts": "2026-09-14T20:00:01+08:00", "method": "browsingContext.navigate",
             "domain": "b.test", "action": None, "result": "denied", "ms": 1},
        ]
        with Harness(lambda m, p: {"entries": entries}) as h:
            out = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("audit")
        self.assertEqual(code, 0)
        lines = [line for line in out.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 2, "一行一条，可以直接 | jq")
        self.assertEqual(json.loads(lines[0])["domain"], "a.test")
        self.assertEqual(json.loads(lines[1])["result"], "denied")

    def test_limit_is_passed_through_as_a_number(self):
        seen: list[dict] = []

        def handler(method, params):
            seen.append({"method": method, "params": params})
            return {"entries": []}

        with Harness(handler) as h:
            with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(h.cli("audit", "--limit", "5"), 0)
        self.assertEqual(seen[0]["method"], "lg:audit.read")
        self.assertEqual(seen[0]["params"], {"limit": 5})

    def test_a_bad_limit_exits_2_without_sending(self):
        with Harness(lambda m, p: {"entries": []}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("audit", "--limit", "many")
            self.assertEqual(h.browser.seen, [])
        self.assertEqual(code, 2)

    def test_clear_reports_how_many_went(self):
        with Harness(lambda m, p: {"cleared": 7}) as h:
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code = h.cli("audit", "clear")
        self.assertEqual(code, 0)
        self.assertIn("7", err.getvalue())

    def test_an_unknown_subcommand_exits_2(self):
        with Harness(lambda m, p: {"entries": []}) as h:
            with mock.patch("sys.stderr", io.StringIO()):
                code = h.cli("audit", "nonsense")
        self.assertEqual(code, 2)

    def test_without_a_browser_it_is_exit_3_like_every_other_command(self):
        """审计在插件里，插件不在就真的读不到 —— 不能假装给一个空列表。"""
        with Harness(None) as h:
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code = h.cli("audit")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(err.getvalue())["error"], ERR_NOT_CONNECTED)


if __name__ == "__main__":
    unittest.main()
