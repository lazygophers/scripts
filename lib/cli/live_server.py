"""live_server — 把目录当静态网站服务（前台驻留，Ctrl-C 停）

用法:
  live_server [目录] [--host H] [--port P] [--tls] [--cert C --key K]
              [--auth USER:PASS] [--spa]
  live_server map <file.geojson> [--host H] [--port P] [--tls] ...

默认 127.0.0.1、随机空闲端口；启动后把完整 URL 打到 stderr。
`--skills` 输出面向 AI 的用法与变化流协议。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.ui import timed

SKILLS = """live_server — 把目录当静态网站服务（前台驻留，Ctrl-C 停）。

- 起服务: `live_server [目录]`；默认 127.0.0.1 + 随机空闲端口，URL 打在 stderr
- 固定端口: `live_server --port 8000`；换绑定: `--host 0.0.0.0`
- 上传: `curl -T file.txt http://127.0.0.1:<port>/file.txt`（PUT，需路径在服务根内）
- 鉴权: `--auth user:pass`（Basic Auth，全路径生效；无 TLS 时为明文）
- HTTPS: `--tls`（mkcert 优先，openssl 自签兜底）；自有证书 `--cert C --key K`
- SPA 回退: `--spa` 时仅无扩展名的 404 回退 index.html（状态码 200）
- 地图: `live_server map x.geojson` 用 OpenLayers 渲染 GeoJSON（起服务前先 lint，坏文件直接给原因不服务）
- lint: `live_server map check x.geojson` 按 RFC 7946 校验并逐条输出原因，退出码 0/1
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
    p.add_argument("directory", nargs="?", default=".",
                   help="服务根目录（默认当前目录）")
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


def _cmd_check(map_file: Path) -> int:
    """geojson lint：逐条打 ERROR 行，合法静默（AI 环境约定）。"""
    import json

    from lib.ai_env import is_ai_shell_env
    from lib.live_server import geojson_errors

    try:
        data = json.loads(map_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _say(f"ERROR: 不是合法 JSON: {e}")
        return 2
    errors = geojson_errors(data)
    if not errors:
        if not is_ai_shell_env():
            _say(f"OK: {map_file.name} 符合 GeoJSON 规范（RFC 7946）")
        return 0
    for e in errors:
        _say(f"ERROR: {e}")
    _say(f"共 {len(errors)} 处不符合规范")
    return 1


def _load_json(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    from lib.live_server import Config, geojson_errors, run
    from lib.notify import consume_debug, consume_dry_run, consume_no_say
    from lib.skills_help import consume_skills

    argv = consume_dry_run(
        consume_skills(
            consume_debug(consume_no_say(list(sys.argv if argv is None else argv))),
            SKILLS))
    args = argv[1:]

    map_file = None
    if args and args[0] == "map":
        if len(args) >= 2 and args[1] == "check":
            # lint 模式: live_server map check <file.geojson>
            if len(args) < 3:
                _say("ERROR: 用法: live_server map check <file.geojson>")
                return 2
            return _cmd_check(Path(args[2]).expanduser().resolve())
        if len(args) < 2:
            _say("ERROR: 用法: live_server map <file.geojson> | "
                 "live_server map check <file.geojson>")
            return 2
        map_file = Path(args[1]).expanduser().resolve()
        if not map_file.is_file():
            _say(f"ERROR: 找不到 {map_file}")
            return 2
        args = ["."] + args[2:]  # map 模式 root = 文件所在目录
        errors = geojson_errors(_load_json(map_file))
        if errors:
            for e in errors:
                _say(f"ERROR: {e}")
            _say(f"共 {len(errors)} 处不符合规范；跑 `live_server map check "
                 f"{map_file}` 看完整说明")
            return 2

    parsed = _parse_args(args)
    root = Path(parsed.directory).expanduser().resolve()
    if not root.is_dir():
        _say(f"ERROR: 找不到目录: {parsed.directory}")
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
                 auth=auth, spa=parsed.spa, map_file=map_file,
                 open_browser=not parsed.no_open)
    return timed(run, label="live_server")(cfg, _say)
