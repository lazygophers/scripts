"""browse — 用命令行驱动浏览器扩展

常用：
  browse install                            # 第一次用：构建扩展 + 注册通信配置 + 指引你加载扩展
  browse daemon start                       # 起中转服务（幂等；平时不用手动跑）
  browse browsingContext getTree --table    # 看浏览器连上没有、有哪些标签页
  browse browsingContext navigate https://example.com
  browse page snapshot --table              # 这一页能点/能填的元素 + 可用 locator
  browse input click 'text=登录'
  browse stop                               # 中止在途指令（daemon 留着）
  browse audit --limit 20 --table           # 看最近 20 条审计
  browse script evaluate 'document.title'
  browse network subscribe --match-url '*/api/*' --duration 30s   # 事件流 JSONL
  browse run 'browsingContext.navigate https://a.com' \
             'browsingContext.navigate https://b.com'             # 并发批量
  browse run -                              # 从 stdin 读，一行一条

结果走 stdout 纯 JSON（可 `| jq`），进度与错误走 stderr，`--table` 出表格。
退出码：0 成功 / 1 指令失败 / 2 参数错误 / 3 浏览器未连接 / 4 用户拒绝确认。

调用形态是固定的 `browse <module> <action> [位置参数...] [--参数 值...]`
（`.scratch/browser-control-extension/spec.md` 6.2）。`--参数` 的名字按 kebab →
camel 转成线上 params 的 key（`--match-url` → `matchUrl`），值先当 JSON 解，解不动
就当字符串：所以 `--index 3` 是数字 3，`--domain example.com` 是字符串。要强行传字
符串 `"123"` 就把引号带上：`--text '"123"'`。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import time

from lib import browse_daemon
from lib.browse_daemon import (
    ABORT_METHOD,
    IDLE_TIMEOUT,
    connect,
    pack,
    probe,
    read_frame,
    socket_path,
)
from lib.browse_protocol import (
    ERR_NOT_CONNECTED,
    ERR_USER_REJECTED,
    MAX_INCOMING_FRAME_BYTES,
    ProtocolError,
    command,
)
from lib.notify import consume_debug, consume_dry_run, consume_no_say
from lib.skills_help import consume_skills
from lib.ui import reporter, timed

# 提权/自举重跑的是用户实际敲的那个命令（仓库里的 bin/browse，或装成包后 PATH 上的
# 入口），不是 lib/cli/ 下的实现模块——后者单独 python 起不来。
SCRIPT_PATH = pathlib.Path(sys.argv[0]).resolve()

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_CONNECTED = 3
EXIT_REJECTED = 4

# 每个线上方法的位置参数名，顺序即命令行上的顺序。这张表同时是「有哪些指令」的唯一
# 事实来源：不在表里的 <module> <action> 直接按参数错误退出，不往 daemon 上发。
# 内容与扩展侧的命令表一一对应（`browser-extension/extension/src/handlers/index.ts`
# 的 HANDLERS），spec 5.1 的 v1 全集。
METHODS: dict[str, tuple[str, ...]] = {
    "browsingContext.getTree": (),
    "browsingContext.create": ("url",),
    "browsingContext.close": (),
    "browsingContext.activate": (),
    "browsingContext.navigate": ("url",),
    "browsingContext.reload": (),
    "browsingContext.captureScreenshot": (),
    "script.evaluate": ("expression",),
    "script.callFunction": ("functionDeclaration",),
    "input.click": ("selector",),
    "input.type": ("selector", "text"),
    "input.key": ("key",),
    "input.scroll": (),
    "storage.getCookies": (),
    "storage.setCookie": ("url", "name", "value"),
    "storage.deleteCookies": (),
    "storage.getLocalStorage": ("key",),
    "storage.setLocalStorage": ("key", "value"),
    "network.subscribe": (),
    "network.unsubscribe": ("subscription",),
    "lg:history.search": ("text",),
    "lg:history.delete": ("url",),
    "lg:bookmarks.search": ("query",),
    "lg:bookmarks.create": ("url",),
    "lg:bookmarks.remove": ("id",),
    "lg:downloads.start": ("url",),
    "lg:downloads.list": (),
    "lg:downloads.cancel": ("id",),
    "lg:page.snapshot": (),
    # 审计存在插件的 chrome.storage.local 里，这两条是把它捞出来的唯一一条路
    # （`browse audit`）。2026-09-14 的架构反转之后 Python 侧不再有审计文件。
    "lg:audit.read": (),
    "lg:audit.clear": (),
}

# 这些 --flag 是 CLI 自己的，不进 params。都与线上参数名不冲突（对着 METHODS 的
# handlers 逐个核过），所以不需要再加前缀去区分。
CLI_FLAGS = frozenset({"table", "socket", "concurrency", "failFast", "duration",
                       "idleTimeout", "limit"})

DEFAULT_CONCURRENCY = 4
# 自举起 daemon 后等它把 socket 建起来的上限。这不是轮询别人的异步结果，是本地进程
# 的就绪等待。
SPAWN_TIMEOUT = 5.0
# 自举是后台起进程、日志丢弃的，起不来时看不到原因；把手动前台跑的命令写进报错里。
SPAWN_HINT = "看具体原因：把 `browse daemon run --socket <path>` 放前台跑一遍"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?$")


class UsageError(Exception):
    """命令行本身写错了 —— 一条都不发出去，退出码 2。"""


# ---------------------------------------------------------------- 参数解析
def _camel(name: str) -> str:
    """`match-url` → `matchUrl`。线上 params 一律 camelCase。"""
    head, *rest = name.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _coerce(raw: str):
    """值先按 JSON 解，解不动就是字符串。

    所以 `--index 3` 给数字、`--all true` 给布尔、`--types '["xhr"]'` 给数组，而
    `--domain example.com` 这种解不成 JSON 的原样当字符串。要强行传字符串形态的数字
    就把 JSON 引号带上：`--text '"123"'`。
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def split_tokens(tokens: list[str]) -> tuple[list[str], dict]:
    """把一串 token 拆成 (位置参数, flag 字典)。flag 的 key 已经转成 camelCase。

    `--x v` / `--x=v` 取值；`--x` 后面没值（或紧跟另一个 `--`）当 True；`--no-x`
    当 False；`--` 之后全部按位置参数吃掉。
    """
    positional: list[str] = []
    flags: dict = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            positional.extend(tokens[i + 1:])
            break
        if not token.startswith("--"):
            positional.append(token)
            i += 1
            continue
        name, eq, raw = token[2:].partition("=")
        if not name:
            raise UsageError(f"空的选项名: {token!r}")
        if eq:
            flags[_camel(name)] = _coerce(raw)
        elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            flags[_camel(name)] = _coerce(tokens[i + 1])
            i += 1
        elif name.startswith("no-"):
            flags[_camel(name[3:])] = False
        else:
            flags[_camel(name)] = True
        i += 1
    return positional, flags


def resolve_method(module: str, action: str) -> str:
    """`history` + `search` → `lg:history.search`。私有能力的 `lg:` 前缀可省。"""
    for candidate in (f"{module}.{action}", f"lg:{module}.{action}"):
        if candidate in METHODS:
            return candidate
    raise UsageError(f"没有这条指令: {module} {action}（`browse --help` 看全部）")


def parse_command(tokens: list[str]) -> tuple[str, dict, dict]:
    """`['input','click','text=登录','--index','1']` → (method, params, CLI 选项)。"""
    if len(tokens) < 2:
        raise UsageError("要写成 `browse <module> <action> [参数...]`")
    method = resolve_method(tokens[0], tokens[1])
    positional, flags = split_tokens(tokens[2:])
    names = METHODS[method]
    if len(positional) > len(names):
        extra = " ".join(positional[len(names):])
        raise UsageError(f"{method} 最多吃 {len(names)} 个位置参数，多出来的: {extra}")
    params = {name: _coerce(value) for name, value in zip(names, positional)}
    opts = {key: flags.pop(key) for key in list(flags) if key in CLI_FLAGS}
    params.update(flags)
    return method, params, opts


def parse_run_item(line: str) -> tuple[str, dict]:
    """`run` 的一条指令串 → (method, params)。

    转义规则选 **shell 风格（`shlex`）**，不另开 JSON 数组那一套。理由：flag 的值
    本来就走 JSON 解析（`_coerce`），嵌套对象写 `--entries '{"a":1}'` 已经能表达，
    再加一套 JSON 数组语法是第二套语法换零能力；而 spec 6.2 / 6.8 的例子、用户手敲
    的样子，都是 `'browsingContext.navigate https://a.com'` 这种 shell 风格。
    """
    tokens = shlex.split(line)
    if not tokens:
        raise UsageError(f"空指令: {line!r}")
    head, dot, action = tokens[0].rpartition(".")
    if not dot:
        raise UsageError(f"指令要写成 `<module>.<action> [参数...]`: {line!r}")
    method, params, opts = parse_command([head, action, *tokens[1:]])
    if opts:
        raise UsageError(f"`run` 的指令串里不能带 CLI 选项 {sorted(opts)}: {line!r}")
    return method, params


def parse_duration(raw) -> float:
    """`30s` / `2m` / `500ms` / `1.5`（裸数字当秒）→ 秒。"""
    text = str(raw).strip()
    match = _DURATION_RE.match(text)
    if not match:
        raise UsageError(f"时长写不对: {text!r}（例：30s、2m、500ms）")
    return float(match.group(1)) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2) or "s"]


# ---------------------------------------------------------------- daemon 进程
def pid_path(sock: pathlib.Path) -> pathlib.Path:
    return sock.with_name(sock.name + ".pid")


def ensure_daemon(sock: pathlib.Path, *, spawn: bool = True) -> bool:
    """socket 那头有活的 daemon 就直接用，没有就起一个并等它就绪。

    """
    if probe(sock):
        return True
    if not spawn:
        return False
    if not SCRIPT_PATH.is_file():
        raise UsageError(f"找不到 browse 自身的可执行文件 {SCRIPT_PATH}，没法自举 daemon")
    subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH), "daemon", "run", "--socket", str(sock)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + SPAWN_TIMEOUT
    while time.monotonic() < deadline:
        if probe(sock):
            return True
        time.sleep(0.05)
    return False


async def _serve(sock: pathlib.Path, idle_timeout: float) -> bool:
    """前台跑 daemon，收到 SIGTERM/SIGINT 就取消它 —— 取消点在 `wait_stopped()`，
    `browse_daemon.run` 的 finally 会把 socket 收干净，不留死文件给下次误判。"""
    task = asyncio.ensure_future(browse_daemon.run(sock, idle_timeout))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await task
    except asyncio.CancelledError:
        return True


def daemon_run(sock: pathlib.Path, idle_timeout: float) -> int:
    """`browse daemon run`：前台守着，由 `ensure_daemon` 在后台拉起。

    `_serve` 返回 False 表示这个 socket 上已经有另一个 daemon 在跑，这一次没起来 ——
    所以是失败，不是成功。
    """
    if probe(sock):
        # pid 文件写在这里，先确认这一个是我们的——否则下面的 finally 会把正在跑的
        # 那个 daemon 的 pid 文件删掉，`daemon stop` 就再也找不到它
        reporter(stderr=True).err(f"这个 socket 上已经有 daemon 在跑：{sock}")
        return EXIT_FAILED
    pid_file = pid_path(sock)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        return EXIT_OK if asyncio.run(_serve(sock, idle_timeout)) else EXIT_FAILED
    finally:
        pid_file.unlink(missing_ok=True)


def daemon_stop(sock: pathlib.Path) -> int:
    """SIGTERM 掉 daemon 并等 socket 消失。没在跑也算成功（幂等）。"""
    report = reporter(stderr=True)
    if not probe(sock) and not pid_path(sock).exists():
        report.info("daemon 本来就没在跑")
        return EXIT_OK
    try:
        pid = int(pid_path(sock).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        report.err(f"daemon 在跑（{sock}）但读不到 pid 文件 {pid_path(sock)}，请手动结束进程")
        return EXIT_FAILED
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_path(sock).unlink(missing_ok=True)
        sock.unlink(missing_ok=True)
        report.info(f"daemon 进程 {pid} 已经不在了，清掉残留文件")
        return EXIT_OK
    deadline = time.monotonic() + SPAWN_TIMEOUT
    while time.monotonic() < deadline:
        if not probe(sock):
            report.ok(f"daemon 已停（pid {pid}）")
            return EXIT_OK
        time.sleep(0.05)
    report.err(f"发了 SIGTERM 但 daemon（pid {pid}）{SPAWN_TIMEOUT:.0f} 秒内没退")
    return EXIT_FAILED


# ---------------------------------------------------------------- 发指令
async def execute(method: str, params: dict, sock: pathlib.Path, *,
                  duration: float | None = None, stream=None) -> dict:
    """发一条指令，等回包。返回 `{"status": "ok"|"failed", ...}`。

    `duration` 只对 `network.subscribe` 有意义：拿到订阅后继续读事件，一行一个 JSON
    打到 `stream`，到点再用 daemon 发的 `sub-N` 退订（那个 id 拿到什么就回什么，不能
    自己缓存或去解析扩展的内部 id）。
    """
    try:
        reader, writer, _ = await connect("cli", sock)
    except (OSError, ProtocolError) as exc:
        return {"status": "failed", "error": ERR_NOT_CONNECTED,
                "message": f"连不上 daemon（{sock}）: {exc}"}
    try:
        writer.write(pack(command(1, method, params), MAX_INCOMING_FRAME_BYTES))
        await writer.drain()
        reply = await _await_reply(reader, 1)
        if reply.get("type") == "error":
            return {"status": "failed", "error": reply.get("error", ""),
                    "message": reply.get("message", "")}
        result = reply.get("result", {})
        if duration is not None:
            await _stream_events(reader, writer, result.get("subscription"), duration,
                                 stream or sys.stdout)
        return {"status": "ok", "result": result}
    except (OSError, ProtocolError, asyncio.IncompleteReadError) as exc:
        return {"status": "failed", "error": ERR_NOT_CONNECTED,
                "message": f"和 daemon 的连接断了: {exc}"}
    finally:
        writer.close()


async def _await_reply(reader, cmd_id: int) -> dict:
    """读到自己那条回包为止。中间夹带的事件是别人订阅的广播，丢掉。"""
    while True:
        message = await read_frame(reader, MAX_INCOMING_FRAME_BYTES)
        if message.get("type") in ("success", "error") and message.get("id") == cmd_id:
            return message


async def _stream_events(reader, writer, subscription, duration: float, stream) -> None:
    deadline = time.monotonic() + duration
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            message = await asyncio.wait_for(read_frame(reader, MAX_INCOMING_FRAME_BYTES), left)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ProtocolError, OSError):
            break
        if message.get("type") == "event":
            stream.write(json.dumps(message, ensure_ascii=False) + "\n")
            stream.flush()
    if not isinstance(subscription, str):
        return
    with contextlib.suppress(OSError, ProtocolError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        writer.write(pack(command(2, "network.unsubscribe", {"subscription": subscription}),
                          MAX_INCOMING_FRAME_BYTES))
        await writer.drain()
        await asyncio.wait_for(_await_reply(reader, 2), 2.0)


# ---------------------------------------------------------------- run 批量
async def run_batch(items: list[tuple[str, dict]], sock: pathlib.Path, *,
                    concurrency: int, fail_fast: bool) -> list[dict]:
    """并发跑一批，返回与 items 同序的结果。

    fail-fast 的边界在**提交**：第一条失败之后还没开跑的标 `skipped`，已经在途的尽力
    取消并标 `failed`（副作用可能已经发生了，所以不能标成 skipped 假装没做过）。

    「停止提交」不需要额外的标志位：失败的那个 worker 把兄弟 worker 全 cancel 掉，
    剩下的下标就再没人从 `cursor` 里取走，落到最后那行的 `skipped` 兜底。
    """
    results: list[dict | None] = [None] * len(items)
    cursor = iter(range(len(items)))
    workers: list[asyncio.Task] = []

    async def worker() -> None:
        for index in cursor:
            try:
                results[index] = await execute(items[index][0], items[index][1], sock)
            except asyncio.CancelledError:
                results[index] = {"status": "failed", "error": "lg:cancelled",
                                  "message": "已提交给浏览器，被 fail-fast 取消，结果未知"}
                raise
            if fail_fast and results[index]["status"] != "ok":
                me = asyncio.current_task()
                for other in workers:
                    if other is not me:
                        other.cancel()
                return

    workers = [asyncio.ensure_future(worker()) for _ in range(max(1, min(concurrency, len(items) or 1)))]
    await asyncio.gather(*workers, return_exceptions=True)
    return [r or {"status": "skipped", "message": "前面有指令失败，这条没有提交"} for r in results]


def read_stdin_items(text: str) -> list[str]:
    """stdin 一行一条，空行和 `#` 开头的注释跳过。"""
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


# ---------------------------------------------------------------- 输出
def print_result(result: dict, *, table: bool, out=None) -> None:
    out = sys.stdout if out is None else out
    if not table:
        out.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return
    _print_table(result, out)


def _print_table(result: dict, out) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console(file=out)
    rows = next((v for v in result.values()
                 if isinstance(v, list) and v and isinstance(v[0], dict)), None)
    if rows is None:
        table = Table(show_header=False)
        for key, value in result.items():
            table.add_row(str(key), json.dumps(value, ensure_ascii=False))
        console.print(table)
        return
    columns = list(dict.fromkeys(key for row in rows for key in row))
    table = Table()
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*[_cell(row.get(column)) for column in columns])
    console.print(table)


def _cell(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def print_error(outcome: dict, err=None) -> None:
    """错误对象走 stderr，形状按 spec 6.7。"""
    (err or sys.stderr).write(json.dumps({
        "type": "error",
        "error": outcome.get("error", ""),
        "message": outcome.get("message", ""),
    }, ensure_ascii=False) + "\n")


def exit_code_for(outcome: dict) -> int:
    if outcome.get("status") == "ok":
        return EXIT_OK
    return {ERR_NOT_CONNECTED: EXIT_NOT_CONNECTED,
            ERR_USER_REJECTED: EXIT_REJECTED}.get(outcome.get("error", ""), EXIT_FAILED)


HELP = """browse — 用命令行驱动浏览器扩展

用法
  browse <module> <action> [位置参数...] [--参数 值...]
  browse run [--concurrency N] [--no-fail-fast] '<指令串>'... | browse run -
  browse daemon start | stop | status
  browse stop                                       中止在途指令，daemon 留着
  browse audit [--limit N] [--table]                看审计日志（存在插件里）
  browse install | uninstall                        装 / 卸（扩展本体仍需你手动加载一次）

先跑起来
  browse install                                    第一次用：构建 + 注册 + 指引加载扩展
  browse browsingContext getTree --table            看浏览器连上没有、有哪些标签页
  browse browsingContext navigate https://example.com
  browse page snapshot --table                      列出这一页能点/能填的元素
  browse input type 'css=input[name=user]' 'myname'
  browse input click 'text=登录'
  browse script evaluate 'document.title'
  browse storage getCookies --domain example.com
  browse network subscribe --match-url '*/api/*' --duration 30s > api.jsonl
  browse run 'browsingContext.navigate https://a.com' 'browsingContext.navigate https://b.com'

选项（CLI 自己的，其余 --xxx 一律当指令参数发给浏览器）
  --table            结果用表格给人看（默认 stdout 出纯 JSON，可 | jq）
  --socket PATH      指定 daemon 的 socket 文件
  --concurrency N    run 的并发上限，默认 4
  --no-fail-fast     run 的每条各自独立，不因为前面失败就停，整体退出码 0
  --duration 30s     network subscribe 听多久，事件一行一个 JSON
  --limit N          audit 只取最近 N 条
  --debug / --no-say 仓库通用开关

参数怎么写
  --参数名按 kebab → camel 转成线上 key：--match-url 就是 matchUrl
  值先按 JSON 解、解不动当字符串：--index 3 是数字，--domain a.com 是字符串
  要强行传字符串形态的数字，把 JSON 引号带上：--text '"123"'
  定位器四种前缀：css= / text= / xpath= / js=，不写前缀默认 css=
  选哪个标签页：--context <id> > --match-url '<glob>' > 当前活动标签页

确认与审计（都在插件里，不在这边）
  设置页：浏览器的扩展详情 →「扩展程序选项」，或点插件面板上的「设置」
  确认模式 silent 直接执行（默认） / per_domain 每个域名问一次 / always 每次都问
  要问的时候浏览器会弹一个小窗，不点就按拒绝算（退出码 4）
  拒绝名单里的域名一律拒绝，连窗都不弹
  这些设置和审计日志都存在插件的 chrome.storage.local 里 —— daemon 没起来也能改

退出码
  0 成功   1 指令失败   2 参数写错   3 浏览器未连接   4 用户拒绝确认

指令全集"""


def help_text() -> str:
    lines = [HELP]
    for method, names in METHODS.items():
        module, _, action = method.rpartition(".")
        args = "".join(f" <{name}>" for name in names)
        lines.append(f"  browse {module} {action}{args}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 入口
def _sock_of(opts: dict) -> pathlib.Path:
    raw = opts.get("socket")
    return socket_path() if raw in (None, True) else pathlib.Path(str(raw))


def _cmd_daemon(tokens: list[str]) -> int:
    action = tokens[0] if tokens else "status"
    _, flags = split_tokens(tokens[1:])
    sock = _sock_of(flags)
    report = reporter(stderr=True)
    if action == "run":
        return daemon_run(sock, float(flags.get("idleTimeout", IDLE_TIMEOUT)))
    if action == "start":
        if ensure_daemon(sock):
            report.ok(f"daemon 在跑：{sock}")
            return EXIT_OK
        report.err(f"daemon 起不来：{sock}（{SPAWN_HINT}）")
        return EXIT_FAILED
    if action == "stop":
        return daemon_stop(sock)
    if action == "status":
        alive = probe(sock)
        report.info(f"daemon {'在跑' if alive else '没在跑'}：{sock}")
        return EXIT_OK if alive else EXIT_FAILED
    raise UsageError(f"daemon 只有 start / stop / status：{action!r}")


def _cmd_run(tokens: list[str]) -> int:
    raw, flags = split_tokens(tokens)
    sock = _sock_of(flags)
    concurrency = int(flags.get("concurrency", DEFAULT_CONCURRENCY))
    if concurrency < 1:
        raise UsageError(f"--concurrency 至少是 1：{concurrency}")
    fail_fast = flags.get("failFast", True) is not False
    if raw == ["-"]:
        raw = read_stdin_items(sys.stdin.read())
    if not raw:
        raise UsageError("`run` 至少要给一条指令串，或者用 `browse run -` 从 stdin 读")

    # 先全部解析，再决定要不要起 daemon：写错一条就一条都不发，副作用为零
    items = [parse_run_item(line) for line in raw]
    if not ensure_daemon(sock):
        print_error({"error": ERR_NOT_CONNECTED, "message": f"daemon 起不来：{sock}（{SPAWN_HINT}）"})
        return EXIT_NOT_CONNECTED

    outcomes = asyncio.run(run_batch(items, sock, concurrency=concurrency, fail_fast=fail_fast))
    report = [{"index": i, "command": line, **outcome}
              for i, (line, outcome) in enumerate(zip(raw, outcomes))]
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not fail_fast:
        return EXIT_OK
    failed = next((o for o in outcomes if o["status"] == "failed"), None)
    return EXIT_OK if failed is None else exit_code_for(failed)


def _cmd_single(tokens: list[str]) -> int:
    method, params, opts = parse_command(tokens)
    sock = _sock_of(opts)
    duration = parse_duration(opts["duration"]) if "duration" in opts else None
    if duration is not None and method != "network.subscribe":
        raise UsageError("--duration 只对 `browse network subscribe` 有意义")
    if not ensure_daemon(sock):
        print_error({"error": ERR_NOT_CONNECTED, "message": f"daemon 起不来：{sock}（{SPAWN_HINT}）"})
        return EXIT_NOT_CONNECTED

    outcome = asyncio.run(execute(method, params, sock, duration=duration))
    if outcome["status"] != "ok":
        print_error(outcome)
        return exit_code_for(outcome)
    print_result(outcome["result"], table=opts.get("table") is True)
    return EXIT_OK


def _cmd_audit(tokens: list[str]) -> int:
    """`browse audit`：把插件里的审计日志捞出来。

    审计存在扩展的 `chrome.storage.local` 里（2026-09-14 起），命令行读不到那个存储，
    只能让扩展自己把它交出来。所以这条命令**必须浏览器连着才有结果** —— 连不上就是
    退出码 3，和别的指令一个口径。

    默认一行一条 JSON（JSONL，可以直接 `| jq`），`--table` 出表格。
    """
    positional, flags = split_tokens(tokens)
    if positional and positional[0] == "clear":
        outcome = _audit_call("lg:audit.clear", {}, flags)
        if outcome["status"] != "ok":
            print_error(outcome)
            return exit_code_for(outcome)
        reporter(stderr=True).ok(f"已清空 {outcome['result'].get('cleared', 0)} 条审计")
        return EXIT_OK
    if positional:
        raise UsageError(f"audit 只有 `browse audit` 和 `browse audit clear`：{positional[0]!r}")

    params = {}
    if "limit" in flags:
        try:
            params["limit"] = int(flags["limit"])
        except (TypeError, ValueError):
            raise UsageError(f"--limit 要是整数：{flags['limit']!r}") from None
    outcome = _audit_call("lg:audit.read", params, flags)
    if outcome["status"] != "ok":
        print_error(outcome)
        return exit_code_for(outcome)

    entries = outcome["result"].get("entries", [])
    if flags.get("table") is True:
        print_result({"entries": entries}, table=True)
        return EXIT_OK
    # JSONL：一行一条，和 `network subscribe` 的事件流同一个形状
    for entry in entries:
        sys.stdout.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return EXIT_OK


def _audit_call(method: str, params: dict, flags: dict) -> dict:
    sock = _sock_of(flags)
    if not ensure_daemon(sock):
        return {"status": "failed", "error": ERR_NOT_CONNECTED,
                "message": f"daemon 起不来：{sock}（{SPAWN_HINT}）"}
    return asyncio.run(execute(method, params, sock))


def _cmd_stop(tokens: list[str]) -> int:
    """`browse stop`：把在途指令全掐了，daemon 留着（spec 4.5）。

    要停 daemon 本身用 `browse daemon stop`。
    """
    sock = _sock_of(split_tokens(tokens)[1])
    report = reporter(stderr=True)
    if not probe(sock):
        report.info(f"daemon 没在跑（{sock}），没有在途指令")
        return EXIT_OK
    outcome = asyncio.run(execute(ABORT_METHOD, {}, sock))
    if outcome["status"] != "ok":
        print_error(outcome)
        return exit_code_for(outcome)
    report.ok(f"已中止 {outcome['result'].get('aborted', 0)} 条在途指令，daemon 还在跑：{sock}")
    return EXIT_OK


def _cmd_install(tokens: list[str], uninstall: bool) -> int:
    """`browse install` / `browse uninstall`：注册/注销 native messaging host。

    延迟 import：注册表那套只有装扩展的人用得上，`browse --help` 不该为它付钱。
    """
    from lib.browse_install import main as install_main

    return install_main(["browse install", *(["--uninstall"] if uninstall else []), *tokens])


def _main(argv: list[str]) -> int:
    tokens = argv[1:]
    if not tokens or tokens[0] in ("-h", "--help", "help"):
        sys.stderr.write(help_text())
        return EXIT_OK
    try:
        if tokens[0] == "daemon":
            return _cmd_daemon(tokens[1:])
        if tokens[0] == "run":
            return _cmd_run(tokens[1:])
        if tokens[0] == "stop":
            return _cmd_stop(tokens[1:])
        if tokens[0] == "audit":
            return _cmd_audit(tokens[1:])
        if tokens[0] in ("install", "uninstall"):
            return _cmd_install(tokens[1:], uninstall=tokens[0] == "uninstall")
        return _cmd_single(tokens)
    except UsageError as exc:
        reporter(stderr=True).err(str(exc))
        return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    doc = __doc__ or ""
    argv = consume_skills(consume_dry_run(consume_debug(consume_no_say(argv)), doc), description=doc)
    sys.argv = argv
    if "--native-host" in argv[1:]:
        # 浏览器 fork 我们时带这个参数。T03 的实现，延迟 import：没装扩展的人不该
        # 因为这个模块不在就连 `browse --help` 都跑不了。不包 timed —— 这是个长命的
        # stdio 进程，计时行没人看。
        from lib.browse_native_host import main as native_host_main

        return native_host_main(argv)
    return timed(_main, label="browse")(argv)
