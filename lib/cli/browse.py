"""browse — 用命令行驱动浏览器扩展

常用：
  browse status                             # 一条命令看链路：bridge / 插件连接 / 心跳
  browse open https://example.com           # 开新标签页（自动进 browse/ 分组）
  browse list                               # 列出所有标签页
  browse goto https://example.com           # 当前页跳转（别名 navigate）
  browse snapshot                           # 这一页能点/能填的元素 + 可用定位符
  browse click '登录'                        # 按可见文字点（不写前缀默认 text=）
  browse fill 'css=input[name=user]' '我的名字'
  browse text                               # 正文纯文字；html 拿源码
  browse wait '提交' | browse wait --text 完成 | browse wait --url '*ok*'
  browse close 'example.com/*'              # 匹配到的全部关掉；--group 调研 关整组
  browse screenshot                         # 存 ~/Downloads/browse-<时间戳>.png 并打路径
  browse data history '关键词'               # 历史；data 组还有 cookie/书签/下载/阅读清单
  browse net watch --match-url '*/api/*' --duration 30s   # 事件流 JSONL
  browse api gcm token <entity>             # 底层透传，参数形状直接跟扩展 handler
  browse run 'goto https://a.com' 'click 登录'            # 并发批量
  browse stop                               # 中止在途指令（daemon 留着）

结果默认：终端里出表格（Rich），被管道接走出 JSON（--table / --json 强制）。
进度与错误走 stderr；`net watch` 和 `audit` 永远是一行一条 JSON（JSONL，可 | jq）。
退出码：0 成功 / 1 指令失败或等待超时 / 2 参数错误 / 3 浏览器未连接 / 4 用户拒绝确认。

调用形态（spec：.scratch/browse-cli-redesign/spec.md）三层：
  browse <动词>                常用层·平铺（open / close / goto / click / …）
  browse <名词组> <动词>        常用层·分组（tab / page / group / data / net / sys / rec）
  browse api <module> <action> 底层透传
`--参数` 名字按 kebab → camel 转成线上 key（--match-url → matchUrl）；值先按 JSON
解，解不动当字符串：`--index 3` 是数字，`--domain example.com` 是字符串。要强行传
字符串 `"123"` 就把引号带上：`--text '"123"'`。
"""

from __future__ import annotations

import asyncio
import base64
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
import zlib

from lib import browse_bridge, browse_log
from lib.browse_daemon import (
    ABORT_METHOD,
    BROWSERS_METHOD,
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
# 内容与扩展侧的命令表一一对应（`browser-extension/browse/src/handlers/index.ts`
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
    "lg:tabs.group": (),
    "lg:tabs.ungroup": (),
    "lg:tabs.groups": (),
    "lg:tabs.updateGroup": ("group",),
    "lg:page.snapshot": (),
    # 审计存在插件的 chrome.storage.local 里，这两条是把它捞出来的唯一一条路
    # （`browse audit`）。2026-09-14 的架构反转之后 Python 侧不再有审计文件。
    "lg:audit.read": (),
    "lg:audit.clear": (),
    # 2026-09-16 扩容的能力面（扩展侧 handlers/index.ts 的 HANDLERS 逐条对齐）
    "lg:downloads.open": ("id",),
    "lg:pageCapture.saveMhtml": (),
    "lg:capture.recordTab": (),
    "lg:capture.recordStop": ("recording",),
    "lg:capture.recordDesktop": (),
    "lg:offscreen.documents": (),
    "lg:clipboard.read": (),
    "lg:clipboard.write": ("text",),
    "lg:readingList.list": (),
    "lg:readingList.add": ("url", "title"),
    "lg:readingList.update": ("id",),
    "lg:readingList.remove": ("id",),
    "lg:topSites.list": (),
    "lg:search.query": ("text",),
    "lg:idle.state": (),
    "lg:system.info": (),
    "lg:notifications.show": ("title", "message"),
    "lg:notifications.clear": ("id",),
    "lg:power.keepAwake": (),
    "lg:power.release": (),
    "lg:proxy.get": (),
    "lg:proxy.set": ("mode",),
    "lg:proxy.clear": (),
    "lg:permissions.getAll": (),
    "lg:permissions.contains": (),
    "lg:gcm.id": (),
    "lg:gcm.token": ("entity",),
    "lg:gcm.deleteToken": ("entity",),
    "lg:userScripts.register": ("name",),
    "lg:userScripts.list": (),
    "lg:userScripts.unregister": ("name",),
    "lg:userScripts.reset": (),
    "lg:userScripts.world": (),
    "lg:declContent.setRules": (),
    "lg:declContent.clear": (),
    "lg:commands.list": (),
    "lg:sidePanel.open": (),
    "lg:sidePanel.close": (),
    "lg:sidePanel.behavior": (),
    "lg:omnibox.setDefault": ("description",),
    "lg:wauth.attach": (),
    "lg:wauth.detach": (),
    "lg:wauth.complete": ("request", "kind"),
    "lg:printing.respond": ("request",),
}

# 这些 --flag 是 CLI 自己的，不进 params。都与线上参数名不冲突（对着 METHODS 的
# handlers 逐个核过），所以不需要再加前缀去区分。
# 线上要求 string 的 id 类参数（同名参数在别的方法上可能是 number，如
# lg:downloads.cancel 的 id）：裸数字会被 _coerce 解析成 JSON 数字、扩展端拒收。
STRING_NUMERIC_PARAMS: dict[str, tuple[str, ...]] = {
    "lg:bookmarks.remove": ("id",),
    "lg:tabs.group": ("group",),
    "lg:tabs.ungroup": ("group",),
    "lg:tabs.updateGroup": ("group",),
    # 线上是 string 的 id 类参数（2026-09-16 扩容面）
    "lg:capture.recordStop": ("recording",),
    "lg:wauth.complete": ("request",),
    "lg:printing.respond": ("request",),
}

CLI_FLAGS = frozenset({"table", "json", "socket", "concurrency", "failFast", "duration",
                       "idleTimeout", "limit", "browser", "timeout", "url", "group",
                       "context"})

DEFAULT_CONCURRENCY = 4
# 自举起 daemon 后等它把 socket 建起来的上限。这不是轮询别人的异步结果，是本地进程
# 的就绪等待。
SPAWN_TIMEOUT = 5.0
# 自举是后台起进程、日志丢弃的，起不来时看不到原因；把手动前台跑的命令写进报错里。
SPAWN_HINT = "看具体原因：把 `browse bridge run --socket <path>` 放前台跑一遍"
# 服务模式接管被占 socket 前，先给临时 daemon 这么长的体面退出时间。
ADOPT_GRACE = 30.0
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?$")

# ---------------------------------------------------------------- 友好层（spec：.scratch/browse-cli-redesign/spec.md）
GROUP_PREFIX = "browse/"
DEFAULT_GROUP_NAME = "default"
# Chrome 的标签组只认这固定一套色（handlers/tabs.ts 的 COLORS）
GROUP_COLORS = ("grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange")

DEFAULT_WAIT_TIMEOUT = 30.0
WAIT_POLL = 0.2
# `wait --idle` 的「网络安静」定义：连续这么久没有新网络事件
WAIT_IDLE_GAP = 0.5

# 只走 `browse api` 的方法（spec 3.9：普通使用者一年也不会敲一次，或只作为别的
# 命令的内部步骤）。与下面的友好命令合起来必须正好等于 METHODS 全集（测试钉死）。
API_ONLY = frozenset({
    "script.callFunction",
    "lg:offscreen.documents",
    "lg:gcm.id", "lg:gcm.token", "lg:gcm.deleteToken",
    "lg:userScripts.register", "lg:userScripts.list", "lg:userScripts.unregister",
    "lg:userScripts.reset", "lg:userScripts.world",
    "lg:declContent.setRules", "lg:declContent.clear",
    "lg:commands.list",
    "lg:sidePanel.open", "lg:sidePanel.close", "lg:sidePanel.behavior",
    "lg:omnibox.setDefault",
    "lg:wauth.attach", "lg:wauth.detach", "lg:wauth.complete",
    "lg:printing.respond",
    "lg:permissions.contains",
    "lg:readingList.update",
    "lg:audit.read", "lg:audit.clear",
})

# 平铺动词 → (底层方法, 位置参数名)。多步 / 有包装的（open / close / list /
# screenshot / text / html / back / forward / wait）不在这张表里，各走各的函数。
# 进平铺层的判据（spec 2.1）：动词全工具无歧义 + 日常高频，两条同时满足；否则进名词组。
FLAT_SIMPLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "goto": ("browsingContext.navigate", ("url",)),
    "click": ("input.click", ("selector",)),
    "fill": ("input.type", ("selector", "text")),
    "snapshot": ("lg:page.snapshot", ()),
    "reload": ("browsingContext.reload", ()),
    "activate": ("browsingContext.activate", ()),
    "eval": ("script.evaluate", ("expression",)),
}
# 别名：不写进 --help，只保留肌肉记忆（spec 4）
FLAT_ALIASES = {"navigate": "goto", "shot": "screenshot", "script": "eval"}
FLAT_SPECIAL = frozenset({"open", "close", "list", "screenshot", "text", "html",
                          "back", "forward", "wait"})

# 名词组 → action → (底层方法, 位置参数名)。group 组是五条多步命令，走专门函数。
NOUN_GROUPS: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "tab": {
        "save": ("lg:pageCapture.saveMhtml", ("filename",)),
        "list": ("browsingContext.getTree", ()),
        "close": ("browsingContext.close", ()),
    },
    "page": {
        "key": ("input.key", ("key",)),
        "scroll": ("input.scroll", ()),
    },
    # group 组的五条（list/add/rename/color/dissolve）是多步命令，见 GROUP_SPECIAL
    "group": {},
    "data": {
        "cookies": ("storage.getCookies", ()),
        "cookie-set": ("storage.setCookie", ("url", "name", "value")),
        "cookie-del": ("storage.deleteCookies", ()),
        "local-get": ("storage.getLocalStorage", ("key",)),
        "local-set": ("storage.setLocalStorage", ("key", "value")),
        "history": ("lg:history.search", ("text",)),
        "history-del": ("lg:history.delete", ("url",)),
        "bookmarks": ("lg:bookmarks.search", ("query",)),
        "bookmark-add": ("lg:bookmarks.create", ("url",)),
        "bookmark-del": ("lg:bookmarks.remove", ("id",)),
        "downloads": ("lg:downloads.list", ()),
        "download": ("lg:downloads.start", ("url",)),
        "download-cancel": ("lg:downloads.cancel", ("id",)),
        "download-open": ("lg:downloads.open", ("id",)),
        "reading-list": ("lg:readingList.list", ()),
        "reading-add": ("lg:readingList.add", ("url", "title")),
        "reading-del": ("lg:readingList.remove", ("id",)),
        "top-sites": ("lg:topSites.list", ()),
    },
    "net": {
        "watch": ("network.subscribe", ()),
        "unwatch": ("network.unsubscribe", ("subscription",)),
        "proxy": ("lg:proxy.get", ()),
        "proxy-set": ("lg:proxy.set", ("mode",)),
        "proxy-clear": ("lg:proxy.clear", ()),
    },
    "sys": {
        "info": ("lg:system.info", ()),
        "idle": ("lg:idle.state", ()),
        "notify": ("lg:notifications.show", ("title", "message")),
        "notify-clear": ("lg:notifications.clear", ("id",)),
        "awake": ("lg:power.keepAwake", ()),
        "awake-off": ("lg:power.release", ()),
        "clipboard": ("lg:clipboard.read", ()),
        "clipboard-set": ("lg:clipboard.write", ("text",)),
        "search": ("lg:search.query", ("text",)),
        "perms": ("lg:permissions.getAll", ()),
    },
    "rec": {
        "tab": ("lg:capture.recordTab", ()),
        "desktop": ("lg:capture.recordDesktop", ()),
        "stop": ("lg:capture.recordStop", ("recording",)),
    },
}

# group 组的五条是多步命令（名字 → 组 id 的折算在 CLI 侧），在 _cmd_group_special 里
GROUP_SPECIAL = frozenset({"list", "add", "rename", "color", "dissolve"})

# 定位符前缀（locator.ts 的 SCHEMES）；友好层的 click/fill 不写前缀默认按可见文字
# 找（与旧写法的 css= 默认不同，spec 3.1）
_LOCATOR_PREFIXES = ("css=", "text=", "text*=", "xpath=", "js=")

# 管理命令：不属于三层里的任何一层（spec 2）
MANAGEMENT = frozenset({"status", "install", "uninstall", "audit", "bridge", "daemon",
                        "stop", "run"})


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


def resolve_api(module: str, action: str) -> str:
    """`browse api history search` → `lg:history.search`。`lg:` 前缀可省。"""
    for candidate in (f"{module}.{action}", f"lg:{module}.{action}"):
        if candidate in METHODS:
            return candidate
    raise UsageError(f"没有这条指令: api {module} {action}（`browse --help` 看全部）")


def _bind_params(method: str, positional: list[str], flags: dict) -> tuple[dict, dict]:
    """位置参数按 METHODS 的名字绑定，flags 拆成 (指令参数, CLI 选项)。"""
    names = METHODS[method]
    if len(positional) > len(names):
        extra = " ".join(positional[len(names):])
        raise UsageError(f"{method} 最多吃 {len(names)} 个位置参数，多出来的: {extra}")
    params = {name: _coerce(value) for name, value in zip(names, positional)}
    opts = {key: flags.pop(key) for key in list(flags) if key in CLI_FLAGS}
    params.update(flags)
    # context id（`<tabId>[.<frameId>]`）和 getTree 的 root 在线上是 string，但裸数字
    # 会被 _coerce 解析成 JSON 数字，扩展端 asString 直接拒收——统一转回字符串。
    # 显式写 `--context '"123"'` 的老写法本来就是字符串，不受影响。
    for key in ("context", "root", *STRING_NUMERIC_PARAMS.get(method, ())):
        if isinstance(params.get(key), (int, float)):
            params[key] = str(params[key])
    return params, opts


def route(tokens: list[str]) -> tuple[str, dict, dict]:
    """`browse ...` 的 token → (命令名, 指令参数, CLI 选项)。

    命令名形如 `'goto'`（平铺）、`'data cookies'`（名词组）、`'api lg:history.search'`
    （透传）。旧的两段式协议名（`browse browsingContext navigate`）从这层起不存在：
    落不进任何一层就是「没有这条指令」，和敲错任何命令一样（spec 8，硬切）。
    """
    if not tokens:
        raise UsageError("要写成 `browse <命令> [参数...]`（`browse --help` 看全部）")
    head = tokens[0]
    head = FLAT_ALIASES.get(head, head)

    # `tab list` / `tab close` 就是 `list` / `close` 的全名形式（spec 3.2），同一条路
    if head == "tab" and len(tokens) > 1 and tokens[1] in ("list", "close"):
        tokens = [tokens[1], *tokens[2:]]
        head = tokens[0]

    if head == "api":
        if len(tokens) < 3:
            raise UsageError("要写成 `browse api <module> <action> [参数...]`")
        method = resolve_api(tokens[1], tokens[2])
        params, opts = _bind_params(method, *split_tokens(tokens[3:]))
        return f"api {method}", params, opts

    if head in NOUN_GROUPS:
        group = NOUN_GROUPS[head]
        action = tokens[1] if len(tokens) > 1 else ""
        if action in GROUP_SPECIAL and head == "group":
            name = f"group {action}"
            positional, flags = split_tokens(tokens[2:])
            opts = {key: flags.pop(key) for key in list(flags) if key in CLI_FLAGS}
            return name, dict(zip(_GROUP_ARGS[action], (_coerce(v) for v in positional))), opts
        entry = group.get(action)
        if entry is None:
            known = " / ".join(sorted({*group, *GROUP_SPECIAL})) if head == "group" \
                else " / ".join(sorted(group))
            raise UsageError(f"没有这条指令: {head} {action}（{head} 组有: {known}）")
        method, names = entry
        params, opts = _bind_params(method, *split_tokens(tokens[2:]))
        return f"{head} {action}", params, opts

    if head in FLAT_SPECIAL:
        positional, flags = split_tokens(tokens[1:])
        # `wait --gone <target>`：--gone 是模式开关，不是它自己的字符串参数。
        if head == "wait" and flags.get("gone") not in (None, True, False):
            positional.insert(0, str(flags.pop("gone")))
            flags["gone"] = True
        opts = {key: flags.pop(key) for key in list(flags) if key in CLI_FLAGS}
        params = {_FLAT_ARGS[head][i]: _coerce(v) for i, v in enumerate(positional)
                  if i < len(_FLAT_ARGS[head])}
        if len(positional) > len(_FLAT_ARGS[head]):
            raise UsageError(f"{head} 最多吃 {len(_FLAT_ARGS[head])} 个位置参数，多出来的: "
                             f"{' '.join(positional[len(_FLAT_ARGS[head]):])}")
        params.update(flags)
        return head, params, opts

    if head in FLAT_SIMPLE:
        method, names = FLAT_SIMPLE[head]
        params, opts = _bind_params(method, *split_tokens(tokens[1:]))
        if head in ("click", "fill") and isinstance(params.get("selector"), str):
            # 友好层默认按可见文字找；要 CSS 就写全 `css=...`（spec 3.1）
            if not params["selector"].startswith(_LOCATOR_PREFIXES):
                params["selector"] = f"text={params['selector']}"
        return head, params, opts

    raise UsageError(f"没有这条指令: {' '.join(tokens[:2])}（`browse --help` 看全部）")


# 平铺多步命令的位置参数名（spec 3.1）
_FLAT_ARGS: dict[str, tuple[str, ...]] = {
    "open": ("url",),
    "close": ("target",),
    "list": (),
    "screenshot": ("file",),
    "text": (),
    "html": (),
    "back": (),
    "forward": (),
    "wait": ("target",),
}
# group 组五条命令的位置参数名（spec 3.4）
_GROUP_ARGS: dict[str, tuple[str, ...]] = {
    "list": (),
    "add": ("name",),
    "rename": ("old", "new"),
    "color": ("name", "color"),
    "dissolve": ("name",),
}


def parse_run_item(line: str) -> tuple[str, dict]:
    """`run` 的一条指令串 → (命令名, params)。shell 风格转义（shlex），同单条命令。"""
    tokens = shlex.split(line)
    if not tokens:
        raise UsageError(f"空指令: {line!r}")
    name, params, opts = route(tokens)
    if "context" in opts:
        params["context"] = str(opts.pop("context"))
    if opts:
        raise UsageError(f"`run` 的指令串里不能带 CLI 选项 {sorted(opts)}"
                         "（选页用 --context <id>）: " + repr(line))
    _runnable(name, params)  # 多步命令（open/close/wait…）在这里就拒，别等发出去一半
    return name, params


def browser_of(opts: dict) -> str:
    """`--browser` 的值，没给就是空串（= 让 daemon 替我挑）。

    一台机器上可以同时连着好几个浏览器（`browse install` 默认给每个都注册）。只有一个
    连着时不用写这个参数；多个连着又不写，daemon 会报错并把都有谁列出来。
    """
    raw = opts.get("browser")
    if raw in (None, True):
        if raw is True:
            raise UsageError("--browser 要带值，比如 `--browser chrome`")
        return ""
    return str(raw).strip()


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
        [sys.executable, str(SCRIPT_PATH), "bridge", "run", "--socket", str(sock)],
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
    """前台跑 bridge，收到 SIGTERM/SIGINT 就取消它 —— 取消点在 `wait_stopped()`，
    `browse_bridge.run` 的 finally 会把 socket 收干净，不留死文件给下次误判。"""
    task = asyncio.ensure_future(browse_bridge.run(sock, idle_timeout))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await task
    except asyncio.CancelledError:
        return True


def _kill_occupant(sock: pathlib.Path, report) -> bool:
    """SIGTERM 掉占着 socket 的临时 daemon，pid 复用靠命令行核对防住。

    pid 文件读不到、或那个 pid 现在跑的已经不是 `bridge run --socket <sock>`
    （被系统回收复用了），就不动手，交回调用方按「起不来」报错。
    """
    try:
        pid = int(pid_path(sock).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    command = out.stdout
    if "bridge" not in command or "run" not in command or str(sock) not in command:
        return False
    report.info(f"临时 daemon（pid {pid}）超过体面期还占着 socket，SIGTERM 后接管：{sock}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # 刚好自己退了，正好
    return True


def _adopt_socket(sock: pathlib.Path, report) -> bool:
    """服务模式下等 / 抢占被占的 socket。

    占着的只会是 `ensure_daemon` 临时拉起的 daemon：扩展一连上它就永不空闲退出，
    光等永远等不到（2026-09-22 实测：临时 daemon 连跑 16 小时，launchd 服务在旁边
    每 10 秒失败一次、刷了 742 行日志）。所以先给 `ADOPT_GRACE` 秒体面退出，超时
    SIGTERM 掉再接管。
    """
    deadline = time.monotonic() + ADOPT_GRACE
    while probe(sock):
        left = deadline - time.monotonic()
        if left > 0:
            time.sleep(min(1.0, left))
            continue
        if not _kill_occupant(sock, report):
            return False
        wait_until = time.monotonic() + SPAWN_TIMEOUT
        while probe(sock) and time.monotonic() < wait_until:
            time.sleep(0.05)
        return not probe(sock)
    return True


def daemon_run(sock: pathlib.Path, idle_timeout: float) -> int:
    """`browse bridge run`（`daemon run` 是它的旧名）：前台守着，由 `ensure_daemon` 拉起。

    `_serve` 返回 False 表示这个 socket 上已经有另一个 daemon 在跑，这一次没起来 ——
    所以是失败，不是成功。唯一例外是服务模式（`idle_timeout <= 0`）：目标状态是
    「这个 socket 上常驻一个 daemon」，现在占着的退了之后就算达成，见 `_adopt_socket`。
    """
    report = reporter(stderr=True)
    if probe(sock):
        if idle_timeout > 0:
            # pid 文件在下面才写，正是为了不动正在跑的那个 daemon 的 pid 文件
            report.err(f"这个 socket 上已经有 daemon 在跑：{sock}")
            return EXIT_FAILED
        if not _adopt_socket(sock, report):
            report.err(f"这个 socket 上已经有 daemon 在跑，接管失败：{sock}")
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
                  duration: float | None = None, stream=None, browser: str = "") -> dict:
    """发一条指令，等回包。返回 `{"status": "ok"|"failed", ...}`。

    `duration` 只对 `network.subscribe` 有意义：拿到订阅后继续读事件，一行一个 JSON
    打到 `stream`，到点再用 daemon 发的 `sub-N` 退订（那个 id 拿到什么就回什么，不能
    自己缓存或去解析扩展的内部 id）。
    """
    try:
        reader, writer, _ = await connect("cli", sock, browser=browser)
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
                    concurrency: int, fail_fast: bool, browser: str = "") -> list[dict]:
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
                results[index] = await execute(items[index][0], items[index][1], sock,
                                               browser=browser)
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

用法（三层，spec：.scratch/browse-cli-redesign/spec.md）
  browse <动词>              常用层·平铺
  browse <名词组> <动词>      常用层·分组
  browse api <module> <action>   底层透传

先跑起来
  browse install            第一次用：构建 + 注册 + 指引加载扩展（装完/升级要重启浏览器）
  browse status             看链路：bridge / 浏览器连接 / 注册三环
  browse open https://example.com
  browse list               列出所有标签页
  browse click '登录'

常用·平铺
  browse open <url>                      开新标签页；自动进分组（--group 名字 / --no-group 不分）
  browse close [网址通配]                 关匹配到的所有页；--group <名字> 关整组；不给关当前页
  browse goto <url>                      原地跳转（别名 navigate）
  browse list                            所有标签页：id / 标题 / 网址 / 分组 / 窗口
  browse click <目标>                    点一下；不写前缀默认按可见文字找
  browse fill <目标> <文字>               填输入框
  browse screenshot [文件]               截当前视口，默认存 ~/Downloads；--base64 才吐 base64
  browse snapshot                        这一页能点/能填的元素 + 可用定位符
  browse text                            正文纯文字（给人和 AI 读的）
  browse html                            页面源码
  browse eval <js>                       在页面里跑一段 JS 拿返回值（别名 script）
  browse wait <目标>                     等元素出现；--gone 等消失 / --text 文字 / --url 通配 / --idle 网络安静
  browse back                              后退（forward 前进；页面 JS 调不动跨域历史时会失败）
  browse reload                            刷新（--ignore-cache 跳过缓存）
  browse activate                          把某个标签页提到前台
  browse run '<指令>'...                 并发批量；`browse run -` 从 stdin 读，一行一条

tab 组（标签页整体）
  browse tab save [文件]                 整页存成单文件存档（MHTML）
  browse tab list | tab close            同 browse list / browse close

page 组（页面内部）
  browse page key <键>                   按一个键（Enter、Escape……）
  browse page scroll                     --dx / --dy 滚动

group 组（标签分组；前缀 browse/ 自动补，默认色按组名哈希）
  browse group list                      分组：名字 / 颜色 / 窗口 / 组内页数
  browse group add <名字>                把当前（或 --url 匹配到的）页加进某组
  browse group rename <旧> <新>
  browse group color <名字> <颜色>        颜色只认 grey/blue/red/yellow/green/pink/purple/cyan/orange
  browse group dissolve <名字>           解散分组，标签页都留着

data 组（浏览器替你记住的东西）
  browse data cookies                    查 cookie（--domain 过滤）
  browse data cookie-set <url> <名> <值> | cookie-del
  browse data local-get <键> | local-set <键> <值>
  browse data history <文字> | history-del <url>
  browse data bookmarks <关键词> | bookmark-add <url> | bookmark-del <id>
  browse data downloads | download <url> | download-cancel <id> | download-open <id>
  browse data reading-list | reading-add <url> <标题> | reading-del <id>
  browse data top-sites

net 组（网络）
  browse net watch --match-url '*/api/*' --duration 30s    事件流一行一个 JSON
  browse net unwatch <订阅号>
  browse net proxy | proxy-set <模式> | proxy-clear

sys 组（浏览器/系统杂项）
  browse sys info | idle | notify <标题> <正文> | notify-clear <id>
  browse sys awake | awake-off           防休眠开/关
  browse sys clipboard | clipboard-set <文字>
  browse sys search <文字> | perms

rec 组（录制）
  browse rec tab | rec desktop | rec stop <录制号>

底层透传
  browse api <module> <action> [参数]    全部 74 个扩展方法都在；lg: 前缀可省
  （例：browse api gcm token <entity>）

管理
  browse status | install | uninstall | audit [--limit N] | bridge start|stop|status|log | stop

选项（CLI 自己的，其余 --xxx 一律当指令参数发给浏览器）
  --context <id>            指定标签页
  --url <通配符>            按网址选标签页（多匹配报错列出候选；close 是全部作用）
  --group <名字>            按分组选（open 时是「放进哪个组」）
  --browser <名字>          发给哪个浏览器；多个连着又不写会报错并列出都有谁
  --table / --json          强制输出格式；默认终端出表格、管道出 JSON
  --timeout 10s             wait 的超时（默认 30s）
  --socket PATH | --concurrency N | --no-fail-fast | --duration 30s | --limit N
  --debug / --no-say        仓库通用开关

参数怎么写
  --参数名按 kebab → camel 转成线上 key：--match-url 就是 matchUrl
  值先按 JSON 解、解不动当字符串：--index 3 是数字，--domain a.com 是字符串
  要强行传字符串形态的数字，把 JSON 引号带上：--text '"123"'
  定位器四种前缀：text= / css= / xpath= / js=；click/fill 不写前缀默认 text=
  选哪个标签页：--context <id> > --url '<glob>' > --group <名字> > 当前活动页

稳定性承诺
  browse api 的参数形状直接跟随扩展 handler，不做任何兼容包装，扩展改了它就跟着改。
  常用层的命令名和参数可以为了手感调整。

退出码
  0 成功   1 指令失败或等待超时   2 参数写错   3 浏览器未连接   4 用户拒绝确认
"""


def help_text() -> str:
    return HELP + "\n"


# ---------------------------------------------------------------- 入口
def _sock_of(opts: dict) -> pathlib.Path:
    raw = opts.get("socket")
    return socket_path() if raw in (None, True) else pathlib.Path(str(raw))


def _cmd_daemon(tokens: list[str]) -> int:
    """`browse bridge ...`（`daemon` 是旧名）。"""
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
        if not probe(sock):
            report.info(f"daemon 没在跑：{sock}")
            return EXIT_FAILED
        report.info(f"daemon 在跑：{sock}")
        # 连着哪些浏览器：多浏览器同时用的时候，这是唯一看得出「--browser 该写什么」的地方
        outcome = asyncio.run(execute(BROWSERS_METHOD, {}, sock))
        if outcome["status"] != "ok":
            print_error(outcome)
            return exit_code_for(outcome)
        names = outcome["result"].get("browsers", [])
        if names:
            report.info(f"连着的浏览器（{len(names)}）：{', '.join(names)}")
            if len(names) > 1:
                report.info("有多个连着，指令要加 --browser <名字> 指定发给谁")
        else:
            report.info("没有浏览器连着：确认浏览器开着且扩展已启用")
        # 装没装成开机自启的服务。跑没跑是上面那行看的，这里只答「重启之后还在不在」
        from lib import browse_install, browse_service
        service = browse_service.status(pathlib.Path.home(), browse_install.platform_key())
        report.info(f"开机自启：{'已装' if service['installed'] else '没装（browse install 可以装）'}")
        # bridge 自己的情况：跑了多久、日志在哪、每条连接多久没动静
        info = asyncio.run(execute(browse_bridge.INFO_METHOD, {}, sock))
        if info["status"] == "ok":
            data = info["result"]
            report.info(f"已跑 {data['uptimeSeconds']} 秒（pid {data['pid']}，端口 {data['port']}）")
            report.info(f"日志：{data['logPath']}（`browse bridge log` 看最近几条）")
        return EXIT_OK
    if action == "log":
        if not probe(sock):
            report.info(f"daemon 没在跑：{sock}")
            report.info(f"日志文件还在，直接看：{browse_log.log_path()}")
            return EXIT_FAILED
        outcome = asyncio.run(execute(
            browse_bridge.LOG_METHOD, {"limit": int(flags.get("limit", 50))}, sock))
        if outcome["status"] != "ok":
            print_error(outcome)
            return exit_code_for(outcome)
        for line in outcome["result"].get("lines", []):
            print(json.dumps(line, ensure_ascii=False))
        return EXIT_OK
    raise UsageError(f"bridge 只有 start / stop / status / log / run：{action!r}")


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
    items = [_runnable(*parse_run_item(line)) for line in raw]
    if not ensure_daemon(sock):
        print_error({"error": ERR_NOT_CONNECTED, "message": f"daemon 起不来：{sock}（{SPAWN_HINT}）"})
        return EXIT_NOT_CONNECTED

    outcomes = asyncio.run(run_batch(items, sock, concurrency=concurrency, fail_fast=fail_fast,
                                     browser=browser_of(flags)))
    report = [{"index": i, "command": line, **outcome}
              for i, (line, outcome) in enumerate(zip(raw, outcomes))]
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not fail_fast:
        return EXIT_OK
    failed = next((o for o in outcomes if o["status"] == "failed"), None)
    return EXIT_OK if failed is None else exit_code_for(failed)


def _runnable(name: str, params: dict) -> tuple[str, dict]:
    """`run` 只吃一步能发出去的命令；多步的（open/close/wait…）单条跑，报参数错误。"""
    if name.startswith("api "):
        return name[4:], params
    if name in FLAT_SIMPLE:
        return FLAT_SIMPLE[name][0], params
    head, _, action = name.partition(" ")
    entry = NOUN_GROUPS.get(head, {}).get(action)
    if entry is not None:
        return entry[0], params
    raise UsageError(f"`run` 不支持 {name}（多步或流式，单条跑）")


# ---------------------------------------------------------------- 友好层执行
def _glob(pattern: str) -> re.Pattern:
    """shell 通配符 → 正则，**不锚定**：`a.com/*` 直接匹配 https://a.com/x。

    扩展侧的 matchUrl（订阅过滤、matchUrl 选页）是锚定的，但 CLI 这边的网址选页
    和 close 按的是 spec 6.2 的写法（`browse close 'a.com/*'`），不带协议也想命中。
    """
    body = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile(body)


def _full_group_name(name: str) -> str:
    """`调研` → `browse/调研`；写全了的不重复补（spec 5.1）。"""
    return name if name.startswith(GROUP_PREFIX) else GROUP_PREFIX + name


def _group_color(name: str) -> str:
    """组名哈希取色：同名组永远同色，跨会话稳定（spec 5.2）。"""
    return GROUP_COLORS[zlib.crc32(name.encode("utf-8")) % len(GROUP_COLORS)]


def _want_table(opts: dict) -> bool:
    """TTY 出表格、管道出 JSON，--table / --json 强制（spec 9.1）。"""
    if opts.get("json") is True:
        return False
    if opts.get("table") is True:
        return True
    return sys.stdout.isatty()


def _outcome_or_die(outcome: dict) -> dict:
    if outcome["status"] != "ok":
        print_error(outcome)
        raise _CommandFailed(exit_code_for(outcome))
    return outcome.get("result", {})


class _CommandFailed(Exception):
    """友好层内部：指令已把错误打到 stderr，直接带着退出码出去。"""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


async def _tabs(sock: pathlib.Path, browser: str) -> list[dict]:
    """getTree 的顶层 context（= 标签页；children 是 frame，这里不看）。"""
    result = _outcome_or_die(await execute("browsingContext.getTree", {}, sock, browser=browser))
    return [c for c in result.get("contexts", []) if not c.get("parent")]

async def _tab_groups(sock: pathlib.Path, browser: str) -> list[dict]:
    result = _outcome_or_die(await execute("lg:tabs.groups", {}, sock, browser=browser))
    return result.get("groups", [])


async def _named_group(name: str, sock: pathlib.Path, browser: str) -> dict:
    """按展示名找组。零个或多个（两个窗口同名组）都报错并列出候选。"""
    full = _full_group_name(name)
    hits = [g for g in await _tab_groups(sock, browser) if g.get("title") == full]
    if not hits:
        raise UsageError(f"没有叫 {full} 的分组（`browse group list` 看全部）")
    if len(hits) > 1:
        where = ", ".join(f"窗口 {g.get('window')}" for g in hits)
        raise UsageError(f"{full} 在 {where} 各有一组，Chrome 的组不跨窗口，分不清要哪个")
    return hits[0]


async def _resolve_target(params: dict, opts: dict, sock: pathlib.Path, browser: str) -> None:
    """把 --context / --url / --group 折算成 params['context']（就地改）。

    五级链（spec 6.1）的前三级在 CLI 侧：--context 直通；--url / --group 在这里解析成
    单个标签页。后两级（活动页）不用解析——不给 context，扩展自己挑当前活动页。
    多匹配一律报错列出候选（读写操作打错页比报错更糟，spec 6.2；`close` 走自己的路）。
    """
    if opts.get("context") not in (None, True):
        params["context"] = str(opts["context"])
        return
    if opts.get("url") not in (None, True):
        pattern = _glob(str(opts["url"]))
        hits = [t for t in await _tabs(sock, browser) if pattern.search(t.get("url", ""))]
        if not hits:
            have = "\n".join(f"  {t.get('context')} {t.get('url', '')}" for t in await _tabs(sock, browser))
            raise UsageError(f"--url {opts['url']} 一个都没匹配到。现在的标签页：\n{have}")
        if len(hits) > 1:
            many = "\n".join(f"  {t.get('context')} {t.get('lg:title', '')} {t.get('url', '')}" for t in hits)
            raise UsageError(f"--url {opts['url']} 匹配到 {len(hits)} 个标签页：\n{many}")
        params["context"] = hits[0]["context"]
        return
    if opts.get("group") not in (None, True):
        group = await _named_group(str(opts["group"]), sock, browser)
        ids = {str(t) for t in group.get("tabs", [])}
        members = [t for t in await _tabs(sock, browser) if t.get("context") in ids]
        active = [t for t in members if t.get("lg:active")]
        if len(active) == 1:
            params["context"] = active[0]["context"]
        elif len(members) == 1:
            params["context"] = members[0]["context"]
        else:
            many = "\n".join(f"  {t.get('context')} {t.get('lg:title', '')}" for t in members)
            raise UsageError(f"{group.get('title')} 里没有唯一的活动页，先 `browse activate` 选一个：\n{many}")


async def _cmd_open(params: dict, opts: dict, sock: pathlib.Path, browser: str) -> None:
    url = params.pop("url", None)
    if not isinstance(url, str) or not url:
        raise UsageError("`browse open <url>` 要给网址")
    extra = {k: params.pop(k) for k in ("type", "background") if k in params}
    context = _outcome_or_die(
        await execute("browsingContext.create", {"url": url, **extra}, sock, browser=browser)
    )["context"]
    if params.get("noGroup") is True:
        params.pop("noGroup")
        # 扩展会自动把新开的页收进它自己的 "browse" 组；--no-group 就是把这一步退掉
        _outcome_or_die(await execute("lg:tabs.ungroup", {"context": context}, sock, browser=browser))
        print_result({"context": context, "group": None}, table=_want_table(opts))
        return
    full = _full_group_name(str(opts.get("group") or DEFAULT_GROUP_NAME))
    color = params.pop("color", None) or _group_color(full)
    params_g = {"context": context, "title": full, "color": color}
    hits = [g for g in await _tab_groups(sock, browser) if g.get("title") == full]
    if len(hits) > 1:
        where = ", ".join(f"窗口 {g.get('window')}" for g in hits)
        raise UsageError(f"{full} 在 {where} 各有一组，分不清新页进哪个；换个组名或 --no-group")
    if hits:
        params_g["group"] = str(hits[0]["group"])
    grouped = _outcome_or_die(await execute("lg:tabs.group", params_g, sock, browser=browser))
    print_result({"context": context, "group": grouped.get("title"), "color": grouped.get("color")},
                 table=_want_table(opts))


async def _close_contexts(params: dict, opts: dict, sock: pathlib.Path, browser: str) -> list[dict]:
    """`close` 的目标：位置参数网址通配 / --group 整组 / --context / 活动页。

    多匹配全部关掉（收拾动作，多关正是本意，spec 6.2）；零匹配报错列出现在的页。
    """
    target = params.pop("target", None)
    contexts: list[str] | None = None
    if opts.get("context") not in (None, True):
        contexts = [str(opts["context"])]
    elif opts.get("group") not in (None, True):
        group = await _named_group(str(opts["group"]), sock, browser)
        contexts = [str(t) for t in group.get("tabs", [])]
        if not contexts:
            raise UsageError(f"{group.get('title')} 里已经没有标签页了")
    elif target:
        pattern = _glob(str(target))
        hits = [t for t in await _tabs(sock, browser) if pattern.search(t.get("url", ""))]
        if not hits:
            have = "\n".join(f"  {t.get('context')} {t.get('url', '')}" for t in await _tabs(sock, browser))
            raise UsageError(f"{target} 一个都没匹配到。现在的标签页：\n{have}")
        contexts = [t["context"] for t in hits]
    close_params = {k: v for k, v in params.items() if k != "target"}
    if contexts is None:
        return [await execute("browsingContext.close", close_params, sock, browser=browser)]
    return [await execute("browsingContext.close", {**close_params, "context": c},
                          sock, browser=browser) for c in contexts]


async def _cmd_list(opts: dict, sock: pathlib.Path, browser: str) -> None:
    tree = await execute("browsingContext.getTree", {}, sock, browser=browser)
    if tree["status"] != "ok":
        print_error(tree)
        raise _CommandFailed(exit_code_for(tree))
    tabs = [c for c in tree.get("result", {}).get("contexts", []) if not c.get("parent")]
    groups_outcome = await execute("lg:tabs.groups", {}, sock, browser=browser)
    groups = groups_outcome.get("result", {}).get("groups", []) \
        if groups_outcome["status"] == "ok" else []  # Firefox 没有组能力时照样列页
    rows = []
    for tab in tabs:
        ctx = str(tab.get("context", ""))
        group = next((g for g in groups if ctx in {str(t) for t in g.get("tabs", [])}), None)
        rows.append({"id": ctx, "title": tab.get("lg:title", ""), "url": tab.get("url", ""),
                     "group": group.get("title", "") if group else "",
                     "window": group.get("window", "") if group else ""})
    print_result({"tabs": rows}, table=_want_table(opts))


async def _cmd_screenshot(params: dict, opts: dict, sock: pathlib.Path, browser: str) -> None:
    file_arg = params.pop("file", None)
    if params.pop("base64", None) is True:
        result = _outcome_or_die(await execute("browsingContext.captureScreenshot", params,
                                               sock, browser=browser))
        print_result(result, table=_want_table(opts))
        return
    if file_arg in (None, True):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        file_arg = str(pathlib.Path.home() / "Downloads" / f"browse-{stamp}.png")
    result = _outcome_or_die(await execute("browsingContext.captureScreenshot", params,
                                           sock, browser=browser))
    data = result.get("data", "")
    path = pathlib.Path(str(file_arg)).expanduser()
    if not path.suffix and params.get("format") == "jpeg":
        path = path.with_suffix(".jpg")
    path.write_bytes(base64.b64decode(data))
    print_result({"file": str(path), "bytes": len(data) * 3 // 4}, table=_want_table(opts))


_EVAL_WRAPPERS = {
    "text": "document.body ? document.body.innerText : ''",
    "html": "document.documentElement.outerHTML",
    "back": "history.back()",
    "forward": "history.forward()",
}


async def _cmd_eval_wrapped(verb: str, params: dict, opts: dict, sock: pathlib.Path,
                           browser: str) -> None:
    """text / html / back / forward：一段写死的 JS 包一层 script.evaluate（spec 3.1）。"""
    result = _outcome_or_die(await execute("script.evaluate",
                                           {"expression": _EVAL_WRAPPERS[verb], **params},
                                           sock, browser=browser))
    value = result.get("result", {}).get("value")
    if verb in ("text", "html"):
        sys.stdout.write(f"{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}\n")
    else:
        print_result(result)


def _snapshot_hit(entries: list[dict], target: str) -> bool:
    """`wait <目标>` 的匹配：基于 snapshot 条目——text 包含、css/xpath 子串（spec 7）。"""
    if target.startswith("text="):
        return any(str(target[5:]) in str(e.get("text", "")) for e in entries)
    if target.startswith("css="):
        return any(str(target[4:]) in str(e.get("css", "")) for e in entries)
    if target.startswith("xpath="):
        return any(str(target[6:]) in str(e.get("xpath", "")) for e in entries)
    if target.startswith("js="):
        raise UsageError("`wait` 不支持 js= 定位符（页面里跑任意 JS 不是等待该干的事）")
    return any(target in str(e.get("text", "")) for e in entries)


async def _cmd_wait(params: dict, opts: dict, sock: pathlib.Path, browser: str) -> int:
    """纯 CLI 侧轮询（spec 7）：200ms 一拍，默认 30s，超时退出码 1。"""
    gone_value = params.pop("gone", None)
    if gone_value not in (None, True, False):
        if params.get("target") is not None:
            raise UsageError("`wait` 只能给一个元素条件")
        params["target"] = gone_value
        gone_value = True
    modes = [m for m, on in (("target", params.get("target")),
                             ("text", params.get("text")),
                             ("url", opts.get("url")),
                             ("idle", params.get("idle"))) if on not in (None, True, False)]
    if len(modes) != 1:
        raise UsageError("`wait` 要正好给一个条件：默认元素 / --text 文字 / --url 通配 / --idle 网络安静")
    mode = modes[0]
    if mode == "idle":
        return await _cmd_wait_idle(sock, browser,
                                    parse_duration(opts["timeout"]) if "timeout" in opts
                                    else DEFAULT_WAIT_TIMEOUT)
    target = str(params.pop("target", ""))
    text_cond = params.pop("text", None)
    params.pop("idle", None)
    gone = gone_value is True
    if opts.get("context") not in (None, True):
        # wait 的 --url 是等待条件不是选页条件；选页只认 --context
        params["context"] = str(opts["context"])
    timeout = parse_duration(opts["timeout"]) if "timeout" in opts else DEFAULT_WAIT_TIMEOUT
    deadline = time.monotonic() + timeout
    while True:
        if mode == "target":
            hit = await _wait_snapshot(target, params, sock, browser)
        else:
            hit = await _wait_url_or_text(mode, text_cond, opts, params, sock, browser)
        if hit is not None and hit != gone:
            return EXIT_OK
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(WAIT_POLL)
    what = {"target": target, "text": str(text_cond), "url": str(opts.get("url"))}[mode]
    waited = f"等 {mode} {what!r}{' 消失' if gone else ''} 超过 {timeout:g}s"
    tabs = "\n".join(f"  {t.get('context')} {t.get('lg:title', '')} {t.get('url', '')}"
                     for t in await _tabs(sock, browser)) or "  （一个标签页都没有）"
    reporter(stderr=True).err(f"{waited}。当时的标签页：\n{tabs}")
    return EXIT_FAILED


async def _wait_snapshot(target: str, params: dict, sock: pathlib.Path,
                         browser: str) -> bool | None:
    try:
        result = _outcome_or_die(await execute("lg:page.snapshot", params, sock, browser=browser))
    except _CommandFailed:
        return None
    return _snapshot_hit(result.get("elements", []), target)


async def _wait_url_or_text(mode: str, text_cond, opts: dict, params: dict,
                            sock: pathlib.Path, browser: str) -> bool | None:
    if mode == "url":
        pattern = _glob(str(opts["url"]))
        return any(pattern.search(t.get("url", "")) and t.get("lg:active")
                   for t in await _tabs(sock, browser))
    try:
        result = _outcome_or_die(
            await execute("script.evaluate",
                          {"expression": "document.body ? document.body.innerText : ''",
                           **params}, sock, browser=browser))
    except _CommandFailed:
        return None
    value = result.get("result", {}).get("value")
    return str(text_cond) in (value if isinstance(value, str) else "")


async def _cmd_wait_idle(sock: pathlib.Path, browser: str, timeout: float) -> int:
    """`--idle`：订一圈网络事件，连续 WAIT_IDLE_GAP 秒没有新事件就算安静（spec 7 / 13）。"""
    try:
        reader, writer, _ = await connect("cli", sock, browser=browser)
    except (OSError, ProtocolError) as exc:
        print_error({"error": ERR_NOT_CONNECTED, "message": f"连不上 daemon: {exc}"})
        return EXIT_NOT_CONNECTED
    try:
        writer.write(pack(command(1, "network.subscribe", {}), MAX_INCOMING_FRAME_BYTES))
        await writer.drain()
        reply = await _await_reply(reader, 1)
        if reply.get("type") == "error":
            print_error(reply)
            return exit_code_for(reply)
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        quiet = False
        while True:
            if time.monotonic() - last >= WAIT_IDLE_GAP:
                quiet = True
                break
            if time.monotonic() >= deadline:
                break
            try:
                message = await asyncio.wait_for(
                    read_frame(reader, MAX_INCOMING_FRAME_BYTES), WAIT_IDLE_GAP)
                if message.get("type") == "event":
                    last = time.monotonic()
            except asyncio.TimeoutError:
                continue
            except (ProtocolError, OSError, asyncio.IncompleteReadError):
                break
        subscription = reply.get("result", {}).get("subscription")
        if isinstance(subscription, str):
            with contextlib.suppress(OSError, ProtocolError, asyncio.TimeoutError,
                                     asyncio.IncompleteReadError):
                writer.write(pack(command(2, "network.unsubscribe",
                                          {"subscription": subscription}),
                                  MAX_INCOMING_FRAME_BYTES))
                await writer.drain()
                await asyncio.wait_for(_await_reply(reader, 2), 2.0)
        if quiet:
            return EXIT_OK
        reporter(stderr=True).err(
            f"等 idle 超过 {timeout:g}s，网络一直没安静（安静 = 连续 {WAIT_IDLE_GAP:g}s 没有新事件）")
        return EXIT_FAILED
    finally:
        writer.close()


def _cmd_any(tokens: list[str]) -> int:
    name, params, opts = route(tokens)
    sock = _sock_of(opts)
    browser = browser_of(opts)
    duration = parse_duration(opts["duration"]) if "duration" in opts else None
    if duration is not None and "network.subscribe" not in name and name != "net watch":
        raise UsageError("--duration 只对 `browse net watch`（和 api network subscribe）有意义")
    if not ensure_daemon(sock):
        print_error({"error": ERR_NOT_CONNECTED, "message": f"daemon 起不来：{sock}（{SPAWN_HINT}）"})
        return EXIT_NOT_CONNECTED

    async def go() -> int:
        try:
            return await _dispatch_friendly(name, params, opts, sock, browser, duration)
        except _CommandFailed as stop:
            return stop.code

    return asyncio.run(go())


async def _dispatch_friendly(name: str, params: dict, opts: dict, sock: pathlib.Path,
                             browser: str, duration: float | None) -> int:
    # ---- 平铺层
    if name in FLAT_SIMPLE:
        await _resolve_target(params, opts, sock, browser)
        method = FLAT_SIMPLE[name][0]
        result = _outcome_or_die(await execute(method, params, sock, browser=browser))
        if name == "eval":
            value = result.get("result", {}).get("value")
            sys.stdout.write(f"{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}\n")
            return EXIT_OK
        print_result(result, table=_want_table(opts))
        return EXIT_OK
    if name == "open":
        await _cmd_open(params, opts, sock, browser)
        return EXIT_OK
    if name == "close":
        outcomes = await _close_contexts(params, opts, sock, browser)
        ok = [o for o in outcomes if o.get("status") == "ok"]
        failed = [o for o in outcomes if o.get("status") != "ok"]
        for outcome in failed:
            print_error(outcome)
        print_result({"closed": len(ok), "failed": len(failed)}, table=_want_table(opts))
        return EXIT_OK if not failed else EXIT_FAILED
    if name == "list":
        await _cmd_list(opts, sock, browser)
        return EXIT_OK
    if name == "screenshot":
        await _resolve_target(params, opts, sock, browser)
        await _cmd_screenshot(params, opts, sock, browser)
        return EXIT_OK
    if name in _EVAL_WRAPPERS:
        await _resolve_target(params, opts, sock, browser)
        await _cmd_eval_wrapped(name, params, opts, sock, browser)
        return EXIT_OK
    if name == "wait":
        return await _cmd_wait(params, opts, sock, browser)

    # ---- api 透传
    if name.startswith("api "):
        method = name[4:]
        outcome = await execute(method, params, sock, duration=duration, browser=browser)
        if outcome["status"] != "ok":
            print_error(outcome)
            return exit_code_for(outcome)
        print_result(outcome["result"], table=_want_table(opts))
        return EXIT_OK

    # ---- 名词组
    head, _, action = name.partition(" ")
    if head == "group":
        return await _cmd_group_special(action, params, opts, sock, browser)
    if name == "net watch":
        outcome = await execute("network.subscribe", params, sock, duration=duration,
                                browser=browser)
        if outcome["status"] != "ok":
            print_error(outcome)
            return exit_code_for(outcome)
        print_result(outcome["result"], table=_want_table(opts))
        return EXIT_OK
    entry = NOUN_GROUPS.get(head, {}).get(action)
    if entry is None:  # 路由层已挡住，到不了这里
        raise UsageError(f"没有这条指令: {name}")
    method = entry[0]
    await _resolve_target(params, opts, sock, browser)
    result = _outcome_or_die(await execute(method, params, sock, browser=browser))
    print_result(result, table=_want_table(opts))
    return EXIT_OK


async def _cmd_group_special(action: str, params: dict, opts: dict, sock: pathlib.Path,
                             browser: str) -> int:
    if action == "list":
        result = _outcome_or_die(await execute("lg:tabs.groups", {}, sock, browser=browser))
        rows = [{"title": g.get("title", ""), "color": g.get("color", ""),
                 "collapsed": g.get("collapsed", False), "window": g.get("window", ""),
                 "tabs": len(g.get("tabs", []))} for g in result.get("groups", [])]
        print_result({"groups": rows}, table=_want_table(opts))
        return EXIT_OK
    if action == "add":
        name = params.pop("name", None)
        if not isinstance(name, str) or not name:
            raise UsageError("`browse group add <名字>` 要给组名")
        await _resolve_target(params, {k: v for k, v in opts.items() if k != "group"},
                              sock, browser)
        full = _full_group_name(name)
        body = {"title": full, "color": params.pop("color", None) or _group_color(full),
                **params}
        hits = [g for g in await _tab_groups(sock, browser) if g.get("title") == full]
        if len(hits) > 1:
            where = ", ".join(f"窗口 {g.get('window')}" for g in hits)
            raise UsageError(f"{full} 在 {where} 各有一组，分不清进哪个；换个名字")
        if hits:
            body["group"] = str(hits[0]["group"])
        result = _outcome_or_die(await execute("lg:tabs.group", body, sock, browser=browser))
        print_result(result, table=_want_table(opts))
        return EXIT_OK
    if action in ("rename", "color"):
        if action == "rename":
            old, new = params.pop("old", None), params.pop("new", None)
            if not (isinstance(old, str) and isinstance(new, str)):
                raise UsageError("`browse group rename <旧名> <新名>` 要两个名字")
            body = {"title": _full_group_name(new)}
        else:
            old, color = params.pop("name", None), params.pop("color", None)
            if not (isinstance(old, str) and isinstance(color, str)):
                raise UsageError("`browse group color <名字> <颜色>` 要名字和颜色")
            if color not in GROUP_COLORS:
                raise UsageError(f"颜色只认 {', '.join(GROUP_COLORS)}")
            body = {"color": color}
        group = await _named_group(old, sock, browser)
        result = _outcome_or_die(await execute("lg:tabs.updateGroup",
                                               {"group": str(group["group"]), **body},
                                               sock, browser=browser))
        print_result(result, table=_want_table(opts))
        return EXIT_OK
    if action == "dissolve":
        name = params.pop("name", None)
        if not isinstance(name, str) or not name:
            raise UsageError("`browse group dissolve <名字>` 要给组名")
        group = await _named_group(name, sock, browser)
        result = _outcome_or_die(await execute("lg:tabs.ungroup",
                                               {"group": str(group["group"])},
                                               sock, browser=browser))
        print_result(result, table=_want_table(opts))
        return EXIT_OK
    raise UsageError(f"group 只有 {' / '.join(sorted(GROUP_SPECIAL))}：{action!r}")


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
    return asyncio.run(execute(method, params, sock, browser=browser_of(flags)))


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


def _cmd_status(tokens: list[str]) -> int:
    """`browse status`：一条命令看完整条链路。bridge 的连接表是唯一真相。

    每条插件连接一行：浏览器名、connectionId、已连多久、上次心跳几秒前。
    旧 native messaging 的注册（manifest/wrapper）只是残留提示 —— 它们不再是
    连接的一部分，`browse uninstall` 可以清掉。
    """
    from lib import browse_bridge, browse_install

    _, flags = split_tokens(tokens)
    sock = _sock_of(flags)
    report = reporter(stderr=True)

    running = probe(sock)
    report.ok(f"bridge: {'在跑' if running else '没在跑'}（{sock}）")

    conns: list[dict] = []
    if running:
        outcome = asyncio.run(execute(browse_bridge.CONNECTIONS_METHOD, {}, sock))
        if outcome["status"] == "ok":
            conns = outcome["result"].get("connections", [])
    for conn in conns:
        report.ok(
            f"{conn['browser']}: 插件已连接 · connectionId {conn['connectionId']}"
            f" · 已连 {conn['sinceSeconds']} 秒 · 心跳 {conn['idleSeconds']} 秒前")
    if running and not conns:
        report.err("没有任何插件连着：扩展加载后会自动连 bridge（装完/升级扩展要重新加载一次）")
    elif not running:
        report.info("bridge 没在跑，插件连接看不了；随便跑一条指令会自动把它拉起来")

    home = pathlib.Path.home()
    plat = browse_install.platform_key()
    leftovers = [row["browser"] for row in browse_install.install_status(home, plat)
                 if any(m["exists"] for m in row["manifests"])]
    if leftovers:
        report.info(f"旧 native messaging 注册还在（{', '.join(sorted(set(leftovers)))}），"
                    "已不影响连接；`browse uninstall` 可清理")

    return EXIT_OK if (running and conns) else EXIT_FAILED


def _main(argv: list[str]) -> int:
    tokens = argv[1:]
    if not tokens or tokens[0] in ("-h", "--help", "help"):
        sys.stderr.write(help_text())
        return EXIT_OK
    try:
        if tokens[0] in ("bridge", "daemon"):
            return _cmd_daemon(tokens[1:])
        if tokens[0] == "status":
            return _cmd_status(tokens[1:])
        if tokens[0] == "run":
            return _cmd_run(tokens[1:])
        if tokens[0] == "stop":
            return _cmd_stop(tokens[1:])
        if tokens[0] == "audit":
            return _cmd_audit(tokens[1:])
        if tokens[0] in ("install", "uninstall"):
            return _cmd_install(tokens[1:], uninstall=tokens[0] == "uninstall")
        return _cmd_any(tokens)
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
