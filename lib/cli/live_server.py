"""live_server — 把目录当静态网站服务（前台驻留，Ctrl-C 停）

用法:
  live_server [目录] [--host H] [--port P] [--tls] [--cert C --key K]
              [--auth USER:PASS] [--spa]

默认 127.0.0.1、随机空闲端口；起服务后自动开浏览器（--no-open 关），
完整 URL 打到 stderr。`--skills` 输出面向 AI 的用法与变化流协议。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.ui import timed

SKILLS = """live_server — 把目录当静态网站服务（前台驻留，Ctrl-C 停）。

- 起服务: `live_server [目录]`；默认 127.0.0.1 + 随机空闲端口，URL 打在 stderr
- 固定端口: `live_server --port 8000`；换绑定: `--host 0.0.0.0`
- 上传: `curl -T file.txt http://127.0.0.1:<port>/file.txt`（PUT，越界 403）
- 鉴权: `--auth user:pass`（Basic Auth，全路径生效；无 TLS 时为明文）
- HTTPS: `--tls`（mkcert 优先，openssl 自签兜底）；自有证书 `--cert C --key K`
- SPA 回退: `--spa` 时仅无扩展名的 404 回退 index.html（状态码 200）
- 起服务后自动打开系统默认浏览器；`--no-open` 不开
- 变化流协议（SSE，GET /__changes/stream）:
  - Content-Type: text/event-stream；每条事件:
    `id: <seq>\\nevent: change\\ndata: {"path":"<相对路径>","kind":"<modify|create|...>"}\\n\\n`
  - 每 15s 一行 `: ping` 心跳；断开后重连从最新事件继续（不补历史）
  - 页面端拿到事件自行决定如何加载渲染；服务端不注入刷新行为
"""


def _say(msg: str) -> None:
    """服务事件（URL / changed / WARN）走 stderr——stdout 留给数据。"""
    print(msg, file=sys.stderr)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="live_server", description="把目录当静态网站服务")
    p.add_argument("path", nargs="?", default=".", help="目录")
    p.add_argument("--host", default="127.0.0.1",
                   help="绑定地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=0, help="端口（默认随机空闲）")
    p.add_argument("--tls", action="store_true", help="启用 HTTPS")
    p.add_argument("--cert", type=Path, help="TLS 证书路径")
    p.add_argument("--key", type=Path, help="TLS 私钥路径")
    p.add_argument("--auth", metavar="USER:PASS", help="HTTP Basic Auth")
    p.add_argument("--spa", action="store_true",
                   help="无扩展名的 404 回退 index.html（状态码 200）")
    p.add_argument("--no-open", action="store_true",
                   help="不起浏览器（默认自动打开）")
    return p.parse_args(args=argv)


def main(argv: list[str] | None = None) -> int:
    from lib.live_server import Config, run
    from lib.notify import consume_debug, consume_dry_run, consume_no_say
    from lib.skills_help import consume_skills

    argv = consume_dry_run(
        consume_skills(
            consume_debug(consume_no_say(list(sys.argv if argv is None else argv))),
            SKILLS))
    parsed = _parse_args(argv[1:])

    root = Path(parsed.path).expanduser().resolve()
    if not root.is_dir():
        _say(f"ERROR: 找不到目录: {parsed.path}")
        return 2

    if bool(parsed.cert) != bool(parsed.key):
        _say("ERROR: --cert 和 --key 必须一起给")
        return 2
    auth = None
    if parsed.auth:
        user, _, pw = parsed.auth.partition(":")
        auth = (user, pw)
    if auth and not parsed.tls and not parsed.cert:
        _say("WARN: Basic Auth 无 TLS 是明文传输，暴露面大请尽快上 --tls")

    cfg = Config(root=root, host=parsed.host, port=parsed.port,
                 tls=parsed.tls, cert=parsed.cert, key=parsed.key,
                 auth=auth, spa=parsed.spa,
                 open_browser=not parsed.no_open)
    return timed(run, label="live_server")(cfg, _say)
