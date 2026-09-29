"""live_server — 把目录当静态网站服务（前台驻留，Ctrl-C 停）

用法:
  live_server [目录] [--host H] [--port P] [--tls] [--cert C --key K]
              [--auth USER:PASS] [--spa]
  live_server openlayers <file.geojson> [...同上 flags]
  live_server leaflet <file.geojson> [...同上 flags]   # 宽容：非规范 GeoJSON 归一化后渲染
  live_server map check <file.geojson>     # RFC 7946 严格 lint
  live_server leaflet check <file.geojson> # 先按 leaflet 模式归一化再 lint

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
- 地图: `live_server openlayers x.geojson`（OpenLayers）或 `live_server leaflet x.geojson`（Leaflet，宽容模式：要素数组、裸几何自动归一化；自带插件——要素属性 `name` 显示名字（面的名字常驻、装不下自动藏），`style` 当 Leaflet Path 选项，`floor` 出楼层按钮（Z/X 换层），WASD 平移、Q/E 缩放；地图文件旁边有同名 `.tilejson`（TileJSON 3.0.0）就当底图，缺的高缩放级瓦片自动用上一级放大；监听文件变化增量刷新、不重载页面：地图文件改了只增删变过的要素，TileJSON / 瓦片改了只重画底图，视图、楼层、底图选择都保留）
- lint: `live_server map check x.geojson` 按 RFC 7946 严格校验并逐条输出原因，退出码 0/1；`live_server leaflet check x.geojson` 先归一化再校验（校验 leaflet 页面实际拿到的数据）；serve 模式 lint 不过只打 WARN 不拦
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
    p.add_argument("path", nargs="?", default=".",
                   help="目录（serve）/ GeoJSON 文件（map）")
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


def _load_json(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _cmd_check(map_file: Path, lenient: bool = False) -> int:
    """geojson lint：逐条打 ERROR 行，合法静默（AI 环境约定）。

    lenient（`leaflet check`）：先按 leaflet 模式归一化（要素数组、裸几何）
    再校验——校验的是 leaflet 页面实际拿到的数据。
    """
    import json

    from lib.ai_env import is_ai_shell_env
    from lib.live_server import geojson_errors, normalize_geojson

    try:
        data = json.loads(map_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _say(f"ERROR: 不是合法 JSON: {e}")
        return 2
    if lenient:
        try:
            data = normalize_geojson(data)
        except (ValueError, KeyError, TypeError) as e:
            _say(f"ERROR: 归一化失败: {e}")
            return 1
    errors = geojson_errors(data)
    if not errors:
        if not is_ai_shell_env():
            _say(f"OK: {map_file.name} 符合 GeoJSON 规范（RFC 7946）")
        return 0
    for e in errors:
        _say(f"ERROR: {e}")
    _say(f"共 {len(errors)} 处不符合规范")
    return 1


def main(argv: list[str] | None = None) -> int:
    from lib.live_server import Config, geojson_errors, normalize_geojson, run
    from lib.notify import consume_debug, consume_dry_run, consume_no_say
    from lib.skills_help import consume_skills

    argv = consume_dry_run(
        consume_skills(
            consume_debug(consume_no_say(list(sys.argv if argv is None else argv))),
            SKILLS))
    args = argv[1:]

    # 渲染库/检查命令作子命令名（openlayers/leaflet/map check），
    # 其余 flags 只解析一次
    map_mode = bool(args) and args[0] in ("openlayers", "leaflet", "map")
    if map_mode:
        lib = args[0]
        rest = [a for a in args[1:] if a != "check"]
        check = "check" in args[1:]
        parsed = _parse_args(rest or ["."])
    else:
        parsed = _parse_args(args)

    map_file = None
    render_lib = "ol"
    root = Path(parsed.path).expanduser().resolve()
    if map_mode:
        if lib == "map" and not check:
            _say("ERROR: 用法: live_server openlayers <file.geojson> | "
                 "live_server leaflet <file.geojson> | "
                 "live_server map check <file.geojson>")
            return 2
        if check:  # map/openlayers check 严格 lint；leaflet check 先归一化
            return _cmd_check(root, lenient=lib == "leaflet")
        map_file = root
        if not map_file.is_file():
            _say(f"ERROR: 找不到 {map_file}")
            return 2
        root = map_file.parent
        render_lib = "ol" if lib == "openlayers" else "leaflet"
        data = _load_json(map_file)
        if render_lib == "leaflet":
            try:
                data = normalize_geojson(data)
            except (ValueError, KeyError, TypeError) as e:
                _say(f"ERROR: 归一化失败: {e}")
                return 2
        errors = geojson_errors(data)
        if errors:
            for e in errors:
                _say(f"WARN: {e}")
            _say(f"WARN: lint 未过（{len(errors)} 处），仍尝试按 {lib} 渲染；"
                 f"严格校验: live_server map check {map_file}")
    elif not root.is_dir():
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
                 auth=auth, spa=parsed.spa, map_file=map_file,
                 render_lib=render_lib, open_browser=not parsed.no_open)
    return timed(run, label="live_server")(cfg, _say)
