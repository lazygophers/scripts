"""browse native host：浏览器 fork 出来的那半截管道。

浏览器只会按 native messaging manifest fork 一个进程，把 stdin/stdout 给它；用户的
CLI 在另一个进程里（`.scratch/browser-control-extension/spec.md` 3.2）。这个模块就是
把这两头接起来的那根线：

    扩展 ──stdin──▶ native host ──unix socket──▶ daemon ──▶ CLI
    扩展 ◀─stdout── native host ◀─unix socket─── daemon ◀── CLI

**不持任何状态**。被杀了就退出，扩展重连时浏览器会重新 fork 一个；订阅由 daemon 在
下一次 `hello` 之后自己补回去（`lib/browse_daemon.py:_rebuild_subscriptions`）。

三条会直接搞坏通信的红线：

1. **stdout 只能出 native messaging 帧**。任何日志必须显式 `file=sys.stderr`，
   `print` 漏一次就污染协议流。拉起 daemon 的子进程同理，stdout 必须 DEVNULL，
   绝不能继承我们的 stdout。
2. **两个方向的帧上限不一样**：往浏览器写用 `encode_frame`（1 MB，Chrome 对
   host → 浏览器方向的硬限制）；往 daemon 写用 `pack(msg, MAX_INCOMING_FRAME_BYTES)`
   （64 MB，截图走的就是这个方向）。反了截图必炸。
3. **坏帧一律断开**：缓冲状态已经错位，续读只会一路错下去。
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from pathlib import Path

from lib.browse_daemon import (
    MAX_BUFFER_BYTES,
    ROLE_NATIVE_HOST,
    connect,
    pack,
    probe,
    socket_path,
)
from lib.browse_protocol import (
    MAX_INCOMING_FRAME_BYTES,
    ProtocolError,
    decode_frames,
    encode_frame,
)

READ_CHUNK = 65536
# daemon 从 spawn 到 socket 可连的等待窗口。冷启动要起解释器 + import，1 秒不够。
SPAWN_TIMEOUT = 5.0
SPAWN_POLL = 0.05


def log(*parts: object) -> None:
    """唯一允许的输出通道。stdout 是协议流，写一个字节都算污染。"""
    print("browse[native-host]:", *parts, file=sys.stderr)


def daemon_command(entrypoint: str | None = None) -> list[str]:
    """拉起 daemon 的命令行。

    `entrypoint` 就是 CLI 传进来的 `argv[0]` —— 浏览器 fork 的那个可执行文件
    （`bin/browse` 或 uv 装出来的 `browse`），两者都是带 shebang 的 Python 脚本，
    所以用当前解释器显式跑它最稳：不依赖执行位，也不依赖 PATH（浏览器给的环境
    往往没有）。
    """
    target = sys.argv[0] if entrypoint is None else entrypoint
    return [sys.executable, str(Path(target).resolve()), "daemon", "start"]


async def ensure_daemon(path: Path, command: list[str] | None = None,
                        timeout: float = SPAWN_TIMEOUT) -> bool:
    """daemon 在就直接用，不在就 detached spawn 拉起并等它 listen。

    `start_new_session=True` 是必须的：我们是浏览器的子进程，浏览器一关就被收掉，
    daemon 若留在同一个进程组/会话里会跟着陪葬，而 CLI 还指望它常驻。
    """
    if probe(path):
        return True
    argv = daemon_command() if command is None else command
    log("daemon 未运行，拉起:", " ".join(argv))
    subprocess.Popen(  # noqa: S603
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,  # 继承我们的 stdout 就等于让 daemon 往协议流里写
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(SPAWN_POLL)
        if probe(path):
            return True
    log(f"daemon 在 {timeout} 秒内没有起来")
    return False


async def _pipe_reader(fh) -> asyncio.StreamReader:
    """把一个二进制文件对象接成 asyncio 的 StreamReader。"""
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), fh)
    return reader


async def _forward(reader: asyncio.StreamReader, emit, direction: str) -> None:
    """一个方向的转发循环：读字节 → 解帧 → 交给 emit。读到头或坏帧就返回。

    `emit` 负责这个方向自己的帧上限，两个方向不一样（见模块开头第 2 条）。
    """
    buf = b""
    while True:
        try:
            chunk = await reader.read(READ_CHUNK)
        except (ConnectionError, OSError) as exc:
            log(f"{direction} 读失败: {exc}")
            return
        if not chunk:
            return
        buf += chunk
        if len(buf) > MAX_BUFFER_BYTES:
            # 单帧上限拦不住「一直发不完整的帧」，缓冲会无限涨。对端不可信，断开。
            log(f"{direction} 累积缓冲超过 {MAX_BUFFER_BYTES} 字节，断开")
            return
        try:
            messages, buf = decode_frames(buf)
        except ProtocolError as exc:
            log(f"{direction} 坏帧，断开让对端重连: {exc}")
            return
        for message in messages:
            try:
                emit(message)
            except (ProtocolError, ConnectionError, OSError) as exc:
                log(f"{direction} 写失败: {exc}")
                return


async def run(path: Path | None = None, stdin=None, stdout=None,
              command: list[str] | None = None, browser: str = "") -> int:
    """接上 daemon 并双向转发，直到任意一头断开。返回进程退出码。

    `stdin` / `stdout` 是二进制流，缺省用真实的标准输入输出；测试拿管道注入。
    """
    target = socket_path() if path is None else path
    src = sys.stdin.buffer if stdin is None else stdin
    sink = sys.stdout.buffer if stdout is None else stdout

    if not await ensure_daemon(target, command):
        return 1
    try:
        reader, writer, conn_id = await connect(role=ROLE_NATIVE_HOST, path=target,
                                               browser=browser)
    except (OSError, ProtocolError, asyncio.IncompleteReadError) as exc:
        log(f"连不上 daemon: {exc}")
        return 1
    log(f"已连上 daemon，connectionId={conn_id}，browser={browser or '(未指定)'}")

    def to_daemon(message: dict) -> None:
        writer.write(pack(message, MAX_INCOMING_FRAME_BYTES))

    def to_browser(message: dict) -> None:
        # ponytail: 同步写，命令帧被 encode_frame 卡在 1 MB 内，阻塞可忽略；
        # 真要往浏览器推大包时再换成 run_in_executor。
        sink.write(encode_frame(message))
        sink.flush()

    browser_side = asyncio.create_task(_forward(await _pipe_reader(src), to_daemon, "扩展 → daemon"))
    daemon_side = asyncio.create_task(_forward(reader, to_browser, "daemon → 扩展"))
    try:
        await asyncio.wait([browser_side, daemon_side], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (browser_side, daemon_side):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()
    log("转发结束，退出")
    return 0


def browser_of(argv: list[str]) -> str:
    """从 `--browser <名字>` 取出我代表哪个浏览器。

    这个参数是 `browse install` 写死在**每个浏览器各自的 wrapper** 里的，不是用户敲的。
    身份必须这么来：扩展侧猜不得 —— Brave 的 User-Agent 伪装成 Chrome，Edge 只差一个
    `Edg/`，而 `chrome.runtime` 里根本没有「我跑在哪个浏览器上」这种字段。

    升级前装的通用 wrapper 不带这个参数，取到空串 —— daemon 那边会归到 `unknown` 槽，
    照常能用。
    """
    for i, token in enumerate(argv):
        if token == "--browser" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if token.startswith("--browser="):
            return token[len("--browser="):].strip()
    return ""


def main(argv: list[str]) -> int:
    """`browse --native-host` 的入口。`lib/cli/browse.py` 认出该参数后延迟 import 调这里。

    `argv` 是剥掉 `--debug` / `--no-say` 之后的完整命令行，含 `argv[0]` —— 拉起
    daemon 时要重新跑的就是它。返回值直接当退出码。
    """
    return asyncio.run(run(command=daemon_command(argv[0]), browser=browser_of(argv)))


__all__ = ["browser_of", "daemon_command", "ensure_daemon", "log", "main", "run"]
