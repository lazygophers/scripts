"""browse 端到端测试：真进程、真 socket、真退出码。

和 `test_browse_cli.py` 的分工：那边在进程内调 `browse._main()`，验的是解析与分支；
这边把 daemon 和 CLI 都起成**独立子进程**，只有一个假的 native host（`StubHost`，
直连 daemon 的 socket 冒充扩展）。所以这里验的是真正跨进程才成立的东西：
`bin/browse` 这个薄壳跑不跑得起来、stdout 上的 JSON 能不能直接喂给 `json.loads`、
shell 拿到的退出码是不是 spec 6.7 的那几个数。

daemon 子进程的 `HOME` 指向临时目录：安全层的配置和审计日志都按 `Path.home()` 落盘，
不隔离的话测试会写用户的真实配置。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_daemon import probe  # noqa: E402
from lib.browse_protocol import ERR_NOT_CONNECTED, ERR_TIMEOUT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BROWSE = ROOT / "bin" / "browse"
READY_TIMEOUT = 10.0
RUN_TIMEOUT = 30.0


def _frame(message: dict) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(body)) + body


class StubHost(threading.Thread):
    """假的 native host：走 daemon 的 socket 握手成 `native-host`，按 handler 回包。

    handler(method, params) 返回 dict → Success 的 result；返回 (code, message) →
    Error。阻塞式 socket + 手写拆帧，和扩展侧那条链路用的是同一个线格式。
    """

    def __init__(self, path: pathlib.Path, handler):
        super().__init__(daemon=True)
        self.path = path
        self.handler = handler
        self.ready = threading.Event()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def run(self) -> None:
        self._sock.connect(str(self.path))
        self._sock.sendall(_frame({"type": "hello", "role": "native-host"}))
        buf = self._recv_frames(b"")[1]  # 吃掉 hello-ack
        self.ready.set()
        while True:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            messages, buf = self._recv_frames(buf + chunk)
            for message in messages:
                if "type" in message:
                    continue  # daemon 只会发 Command 过来
                self._reply(message)

    def _recv_frames(self, buf: bytes) -> tuple[list[dict], bytes]:
        if not buf:
            buf = self._sock.recv(65536)
        out = []
        while len(buf) >= 4:
            (length,) = struct.unpack("<I", buf[:4])
            if len(buf) < 4 + length:
                break
            out.append(json.loads(buf[4:4 + length].decode("utf-8")))
            buf = buf[4 + length:]
        return out, buf

    def _reply(self, message: dict) -> None:
        outcome = self.handler(message["method"], message.get("params") or {})
        if isinstance(outcome, tuple):
            reply = {"type": "error", "id": message["id"], "error": outcome[0], "message": outcome[1]}
        else:
            reply = {"type": "success", "id": message["id"], "result": outcome}
        self._sock.sendall(_frame(reply))

    def close(self) -> None:
        self._sock.close()


class BrowseE2E(unittest.TestCase):
    """每个用例一套独立的 tmpdir + daemon 子进程，互不串味。"""

    def setUp(self) -> None:
        # /tmp 而不是默认的 $TMPDIR：macOS 的 `/var/folders/...` 路径长，Unix socket
        # 路径有 104 字节上限，叠上文件名就顶格了。
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="browse-e2e-"))
        self.sock = self.tmp / "browse.sock"
        self.env = {**os.environ, "HOME": str(self.tmp), "SCRIPTS_NO_SAY": "1"}
        self.env.pop("XDG_RUNTIME_DIR", None)
        # WS 端口默认钉死 9330（扩展读不到本地文件，双方只能约定常量，见
        # `lib/browse_bridge.py: DEFAULT_WS_PORT`）。这些用例只走 Unix socket 那条
        # 腿（StubHost 假扩展直连 daemon socket），根本用不上 WS，但子进程照样会去
        # bind 9330——跟开发者机器上任何一个真在跑的 `browse bridge` 撞车，daemon
        # 起不来，这批用例全挂且报错和真实业务 bug 长得一样。"0" 让内核分配临时
        # 端口，彻底消除这个环境依赖。
        self.env["BROWSE_BRIDGE_PORT"] = "0"
        self.stub: StubHost | None = None
        self.daemon = subprocess.Popen(
            [sys.executable, str(BROWSE), "daemon", "run", "--socket", str(self.sock),
             "--idle-timeout", "120"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline and not probe(self.sock):
            time.sleep(0.05)
        self.assertTrue(probe(self.sock), f"daemon 没在 {READY_TIMEOUT}s 内起来：{self.sock}")

    def tearDown(self) -> None:
        if self.stub is not None:
            self.stub.close()
        self.daemon.terminate()
        self.daemon.wait(timeout=READY_TIMEOUT)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def attach(self, handler) -> None:
        """挂一个假浏览器上去，等它握完手再返回。"""
        self.stub = StubHost(self.sock, handler)
        self.stub.start()
        self.assertTrue(self.stub.ready.wait(READY_TIMEOUT), "假 native host 没连上 daemon")

    def browse(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BROWSE), *args, "--socket", str(self.sock)],
            env=self.env, capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )

    # ------------------------------------------------------------------ 用例
    def test_get_tree_prints_json_on_stdout(self):
        """最常用的一条：stdout 必须是可直接 `| jq` 的纯 JSON，退出码 0。"""
        tree = {"contexts": [{"context": "42", "url": "https://example.com/", "title": "Example"}]}
        self.attach(lambda method, params: tree if method == "browsingContext.getTree"
                    else ("unknown command", method))

        done = self.browse("api", "browsingContext", "getTree")

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), tree)

    def test_run_fail_fast_reports_three_states(self):
        """`run` 的 fail-fast：失败之前的 ok、失败的 failed、没提交的 skipped。

        `--concurrency 1` 是为了让三态可断言 —— 并发下谁先跑完不确定，而 fail-fast
        的语义（停止**提交**未开始的）只有串行时才有唯一答案。
        """
        def handler(method, params):
            if params.get("url") == "https://b.com":
                # 错误码只能用 BiDi 标准枚举或带冒号的扩展码：协议层会校验，编一个
                # `unknown error` 出来会被当坏帧直接断开连接（`lib/browse_protocol.py`）
                return (ERR_TIMEOUT, "b 站点打不开")
            return {"navigation": params.get("url")}

        self.attach(handler)

        done = self.browse("run", "--concurrency", "1",
                           "goto https://a.com",
                           "goto https://b.com",
                           "goto https://c.com")

        self.assertEqual(done.returncode, 1, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual([item["status"] for item in report], ["ok", "failed", "skipped"])
        self.assertEqual(report[0]["result"], {"navigation": "https://a.com"})
        self.assertEqual(report[1]["error"], ERR_TIMEOUT)
        self.assertEqual(report[1]["message"], "b 站点打不开")
        # 副作用不能假装没发生：c 从没提交过才叫 skipped
        self.assertIn("没有提交", report[2]["message"])

    def test_exit_code_3_when_browser_absent(self):
        """daemon 在跑但浏览器没连上 → 退出码 3，错误对象走 stderr（spec 6.7）。"""
        done = self.browse("api", "browsingContext", "getTree")

        self.assertEqual(done.returncode, 3)
        self.assertEqual(done.stdout, "")
        # stderr 上还有 `timed` 打的那行耗时（仓库约定），只挑 JSON 那行
        payload = json.loads(next(line for line in done.stderr.splitlines() if line.startswith("{")))
        self.assertEqual(payload["error"], ERR_NOT_CONNECTED)


if __name__ == "__main__":
    unittest.main()
