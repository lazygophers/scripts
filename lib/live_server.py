"""live_server — 把一个目录当静态网站服务。

spec: .scratch/live-server/spec.md。核心 = stdlib http.server，叠加：
SPA 回退、Basic Auth、PUT 上传（越界 403）、目录美化页、marked.js
Markdown 渲染、SSE 变化流、HTTPS（mkcert/openssl 自签/透传）。

外部前端依赖（marked.js）一律「远端 CDN 取 + 本地固定
路径缓存」，代码不内嵌第三方 JS（用户 2026-09-29 常设偏好）。
"""
from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit



from lib.notify import is_debug

# CDN 缓存：首次从远端取，落本地固定路径，之后离线可用
CDN_CACHE_ROOT = Path.home() / ".cache" / "lazygophers" / "live-server"
CDN_ASSETS = {
    # 名称 -> 备用远端地址链；serve 于 /_assets/<名称>
    "marked.min.js": (
        "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
        "https://unpkg.com/marked/marked.min.js",
    ),
}

CONFIG_DIR = Path.home() / ".config" / "lazygophers" / "scripts" / "live-server"


# ---------------------------------------------------------------- 缓存与安全
def cached_asset_path(name: str) -> Path:
    return CDN_CACHE_ROOT / name


def fetch_asset(name: str, timeout: float = 10.0) -> Path | None:
    """取 CDN 资产进缓存；已缓存直接返回。失败（含离线）返回 None。"""
    dest = cached_asset_path(name)
    if dest.is_file():
        return dest
    urls = CDN_ASSETS.get(name)
    if not urls:
        return None
    import urllib.request

    CDN_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    for url in urls:  # 主源挂了走备用源
        try:
            if is_debug():
                print(f"asset {name}: GET {url}", file=sys.stderr)
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)  # 原子落盘，读的人永远看到完整文件
            return dest
        except OSError as e:
            if is_debug():
                print(f"asset {name}: {url} 失败: {e}", file=sys.stderr)
    return None


def inside_root(root: Path, target: Path) -> bool:
    """越界判定：resolve 后 target 必须仍在 root 内（含 root 本身）。"""
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- 变化流
class ChangeHub:
    """文件变化事件广播：SSE 订阅者与终端日志的中间层。

    事件只追加、序号单调；SSE handler 按序号等待，天然不丢不重。
    """

    def __init__(self):
        self._cv = threading.Condition()
        self._events: list[tuple[int, str, str]] = []  # (seq, path, kind)
        self._seq = 0

    def publish(self, path: str, kind: str) -> None:
        with self._cv:
            self._seq += 1
            self._events.append((self._seq, path, kind))
            self._cv.notify_all()

    def after(self, seq: int, timeout: float) -> list[tuple[int, str, str]]:
        """返回序号 > seq 的事件；超时返回空表（SSE 用它发心跳）。"""
        with self._cv:
            self._cv.wait_for(lambda: self._seq > seq, timeout)
            fresh = [e for e in self._events if e[0] > seq]
        return fresh

    @property
    def seq(self) -> int:
        with self._cv:
            return self._seq


def start_watcher(root: Path, hub: ChangeHub, on_change=None) -> object | None:
    """起 watchdog 监听；没装 watchdog 则放弃监听（SSE 静默）不致命。"""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return None

    class _H(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            rel = str(Path(event.src_path).relative_to(root))
            hub.publish(rel, event.event_type)
            if on_change:
                on_change(rel)

    obs = Observer(timeout=0.2)
    obs.schedule(_H(), str(root), recursive=True)
    obs.daemon = True
    obs.start()
    return obs


# ---------------------------------------------------------------- 页面模板
_CSS = """ :root{--bg:#fafafa;--fg:#1f2328;--dim:#656d76;--line:#d8dee4;
--accent:#0969da;--hover:#f3f4f6;--drop:#ddf4ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,'PingFang SC',sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}
.crumbs{display:flex;gap:4px;align-items:center;margin-bottom:12px;font-size:15px;flex-wrap:wrap}
.crumbs a{color:var(--accent);text-decoration:none}.crumbs a:hover{text-decoration:underline}
.crumbs .sep{color:var(--dim)}.crumbs .here{font-weight:600}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:var(--dim);font-weight:500;font-size:12px;border-bottom:1px solid var(--line);padding:6px 8px;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--fg)}th.sorted::after{content:" \\2193";color:var(--accent)}
td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
tr.row:hover{background:var(--hover)}
td.name{max-width:420px;overflow:hidden;text-overflow:ellipsis}
td.name a{color:var(--fg);text-decoration:none}td.name a:hover{color:var(--accent)}
td.num{color:var(--dim);font-variant-numeric:tabular-nums}
#drop{margin-top:20px;border:2px dashed var(--line);border-radius:8px;padding:22px;text-align:center;color:var(--dim)}
#drop.on{border-color:var(--accent);background:var(--drop);color:var(--accent)}
.md-body{max-width:860px;margin:0 auto;padding:24px 16px}
.md-body h1{font-size:24px;border-bottom:1px solid var(--line);padding-bottom:8px}
.md-body code{background:#eff1f3;border-radius:4px;padding:2px 5px;font-size:85%}
.md-body pre{background:#f6f8fa;border-radius:8px;padding:12px;overflow:auto}
.md-body blockquote{margin:0;padding:0 12px;color:var(--dim);border-left:3px solid var(--line)}"""

_DIR_ICON = ('<svg width="16" height="16" viewBox="0 0 16 16" fill="#54aeff">'
             '<path d="M1.5 3A1.5 1.5 0 013 1.5h3.2c.4 0 .8.16 1.06.44l.94.94H13'
             'A1.5 1.5 0 0114.5 4.3v8.2A1.5 1.5 0 0113 14H3a1.5 1.5 0 01-1.5-1.5z"/></svg>')
_FILE_ICON = ('<svg width="16" height="16" viewBox="0 0 16 16" fill="#656d76">'
              '<path d="M4 1.5A1.5 1.5 0 015.5 0h5L14 3.5v11A1.5 1.5 0 0112.5 16'
              'h-7A1.5 1.5 0 014 14.5zm2 0v3h4.5L9 3z"/></svg>')


def _page(title: str, body: str, extra_js: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="zh-SG"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head>
<body><main>{body}</main><script>{extra_js}</script></body></html>"""


def render_listing(rel_dir: str, entries: list[dict], here: str) -> str:
    """目录美化页。entries: [{name, is_dir, mtime, size, href}]。原型定基调。"""
    parts = ['<nav class="crumbs">']
    acc = ""
    for i, seg in enumerate(here.strip("/").split("/") if here.strip("/") else []):
        acc += "/" + seg
        parts.append(f'<a href="{acc}/">{seg}</a><span class="sep">/</span>')
    parts.append(f'<span class="here">{rel_dir or "root"}</span></nav>')
    rows = "".join(
        f'<tr class="row"><td class="name">'
        f'<a href="{e["href"]}">{_DIR_ICON if e["is_dir"] else _FILE_ICON} '
        f'{e["name"]}</a></td>'
        f'<td class="num">{e["mtime"]}</td><td class="num">{e["size"]}</td></tr>'
        for e in entries)
    body = (f'{"".join(parts)}<table id="list"><thead><tr>'
            '<th data-k="name">名称</th><th data-k="mtime">修改时间</th>'
            '<th data-k="size">大小</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            '<div id="drop">拖文件到这里上传，松手即传到当前目录</div>')
    js = """
document.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
  const tb = document.querySelector("#list tbody");
  const k = th.dataset.k, idx = ["name","mtime","size"].indexOf(k);
  const rows = [...tb.rows].sort((a,b)=>{
    const x=a.cells[idx].innerText, y=b.cells[idx].innerText;
    return k==="size" ? (parseFloat(x)||0)-(parseFloat(y)||0) : x.localeCompare(y);});
  tb.append(...rows);
  document.querySelectorAll("th").forEach(t=>t.classList.remove("sorted"));
  th.classList.add("sorted");}));
const drop = document.getElementById("drop"), here = %s;
["dragover","dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.toggle("on", ev==="dragover");
  if (ev !== "drop") return;
  [...e.dataTransfer.files].forEach(f => {
    const path = here.replace(/\\/+$/,"") + "/" + f.name;
    fetch(path, {method:"PUT", body:f}).then(r => {
      drop.textContent = r.ok ? "\\u2713 " + f.name + " \\u5df2\\u4f20" :
        "\\u2717 " + f.name + " \\u5931\\u8d25 (" + r.status + ")";
      if (r.ok) setTimeout(()=>location.reload(), 500);
    });});}));""" % json.dumps(here)
    return _page(rel_dir, body, js)


def render_markdown_page(title: str) -> str:
    """`.md` 包装页：加载缓存里的 marked.js 浏览器端渲染。"""
    body = ('<article class="md-body" id="md">加载中…</article>'
            '<script src="/_assets/marked.min.js"></script>'
            '<script>fetch(location.pathname).then(r=>r.text()).then(t=>{'
            'document.getElementById("md").innerHTML = marked.parse(t);'
            'document.title = t.match(/^#\\s*(.+)$/m)?.[1] || %s;});</script>'
            % json.dumps(title))
    return _page(title, body)


# ---------------------------------------------------------------- 服务端
@dataclass
class Config:
    root: Path
    host: str = "127.0.0.1"
    port: int = 0
    tls: bool = False
    cert: Path | None = None
    key: Path | None = None
    auth: tuple[str, str] | None = None
    spa: bool = False
    open_browser: bool = True
    hub: ChangeHub = field(default_factory=ChangeHub, repr=False)


def ensure_cert(cfg: Config, say) -> tuple[Path, Path]:
    """TLS 证书三来源：透传 > mkcert > openssl 自签（spec 票 02）。"""
    if cfg.cert and cfg.key:
        return cfg.cert, cfg.key
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = CONFIG_DIR / "cert.pem", CONFIG_DIR / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    if shutil.which("mkcert"):
        subprocess.run(["mkcert", "-cert-file", str(cert), "-key-file",
                        str(key), "localhost", "127.0.0.1"], check=True)
    elif shutil.which("openssl"):
        say("WARN: 无 mkcert，用 openssl 自签，浏览器需手动信任")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-nodes",
                        "-days", "365", "-subj", "/CN=localhost",
                        "-keyout", str(key), "-out", str(cert)], check=True)
    else:
        raise RuntimeError("无 mkcert 也无 openssl，无法签发 TLS 证书")
    for f in (cert, key):
        os.chmod(f, 0o600)
    return cert, key


def make_server(cfg: Config, say) -> ThreadingHTTPServer:
    """构建监听中的 ThreadingHTTPServer（已含 TLS 若要求）。"""
    srv = ThreadingHTTPServer((cfg.host, cfg.port), _handler_class(cfg, say))
    if cfg.tls:
        cert, key = ensure_cert(cfg, say)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


def _handler_class(cfg: Config, say):
    root = cfg.root.resolve()  # macOS /var -> /private/var 软链，必须先归一
    hub = cfg.hub
    scheme = "https" if cfg.tls else "http"

    class Handler(BaseHTTPRequestHandler):
        # 访问日志默认丢（stderr 噪声）；--debug 放行
        def log_message(self, fmt, *args):
            if is_debug():
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        # ---- 基础 ----
        def _check_auth(self) -> bool:
            if not cfg.auth:
                return True
            want = base64.b64encode(
                f"{cfg.auth[0]}:{cfg.auth[1]}".encode()).decode()
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(f"Basic {want}", got)

        def _deny_auth(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Basic realm="{root.name}"')
            self.end_headers()

        def _reply(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reply_html(self, code: int, html: str):
            self._reply(code, html.encode(), "text/html; charset=utf-8")

        def _resolve(self, rel: str) -> Path | None:
            """URL 路径 -> root 内的绝对路径；越界给 None。"""
            target = (root / rel).resolve()
            return target if inside_root(root, target) else None

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = hub.seq
            try:
                while True:  # 客户端断开靠 BrokenPipeError 收场
                    for seq, path, kind in hub.after(last, 15.0):
                        last = seq
                        payload = json.dumps({"path": path, "kind": kind})
                        self.wfile.write(
                            f"id: {seq}\nevent: change\ndata: {payload}\n\n".encode())
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _asset(self, name: str):
            if name not in CDN_ASSETS:  # 只放行已知资产，不是任意读
                return self._reply(404, b"no such asset", "text/plain")
            path = fetch_asset(name)
            if path is None:
                return self._reply(503, b"asset unavailable (offline?)",
                                   "text/plain")
            self._reply(200, path.read_bytes(),
                        "text/css" if name.endswith(".css") else
                        "application/javascript")

        def _spa_fallback(self, rel: str):
            """--spa：无扩展名 404 回退 index.html，状态码 200（spec 票 07）。"""
            if cfg.spa and not Path(rel).suffix:
                index = self._resolve("index.html")
                if index and index.is_file():
                    return self._reply_html(
                        200, index.read_bytes().decode("utf-8", "replace"))
            return self._reply_html(
                404, _page("404", f"<h1>404</h1><p>{rel}</p>"))

        # ---- GET ----
        def do_GET(self):
            if not self._check_auth():
                return self._deny_auth()
            path = unquote(urlsplit(self.path).path)

            if urlsplit(self.path).path == "/favicon.ico":
                icon = ('<svg xmlns="http://www.w3.org/2000/svg" '
                        'viewBox="0 0 16 16"><text y="13" font-size="13">'
                        "\U0001f310</text></svg>").encode()
                return self._reply(200, icon, "image/svg+xml")
            if path == "/__changes/stream":
                return self._sse()
            if path.startswith("/_assets/"):
                return self._asset(path[len("/_assets/"):])

            rel = path.lstrip("/")
            target = self._resolve(rel)
            if target is None:
                return self._reply(403, b"forbidden", "text/plain")

            if target.is_dir():
                index = target / "index.html"
                if index.is_file():
                    return self._reply_html(
                        200, index.read_bytes().decode("utf-8", "replace"))
                return self._reply_html(200, self._listing(rel, target))

            if target.is_file():
                if target.suffix == ".md":
                    return self._reply_html(
                        200, render_markdown_page(target.name))
                ctype = mimetypes.guess_type(target.name)[0] or \
                    "application/octet-stream"
                return self._reply(200, target.read_bytes(), ctype)

            return self._spa_fallback(rel or "/")

        def _listing(self, rel: str, target: Path) -> str:
            entries = []
            for child in sorted(target.iterdir()):
                st = child.stat()
                entries.append({
                    "name": child.name + ("/" if child.is_dir() else ""),
                    "is_dir": child.is_dir(),
                    "mtime": time.strftime("%m-%d %H:%M",
                                           time.localtime(st.st_mtime)),
                    "size": "-" if child.is_dir() else
                            f"{st.st_size / 1024:.1f} KB",
                    "href": f"/{rel}/{child.name}" if rel else f"/{child.name}",
                })
            return render_listing(rel, entries, "/" + rel)

        # ---- PUT ----
        def do_PUT(self):
            if not self._check_auth():
                return self._deny_auth()
            rel = unquote(urlsplit(self.path).path).lstrip("/")
            target = self._resolve(rel)
            if target is None or target.is_dir():
                return self._reply(403, b"path escapes root", "text/plain")
            length = int(self.headers.get("Content-Length") or 0)
            existed = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as f:
                remaining = length
                while remaining > 0:  # 分块读定长请求体；copyfileobj 会等 EOF
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            rel_disp = str(target.relative_to(root))
            hub.publish(rel_disp, "modify")
            self._reply(200 if existed else 201,
                        f"saved {rel_disp}".encode(), "text/plain")

        def do_DELETE(self):
            if not self._check_auth():
                return self._deny_auth()
            return self._reply(405, b"not allowed", "text/plain")

    return Handler


def run(cfg: Config, say) -> int:
    """起服务、打 URL、前台驻留。Ctrl-C 退出返回 0。"""
    srv = make_server(cfg, say)
    port = srv.server_address[1]
    proto = "https" if cfg.tls else "http"
    say(f"{proto}://{cfg.host}:{port}/")
    if cfg.open_browser:
        import webbrowser

        webbrowser.open(f"{proto}://127.0.0.1:{port}/")
    obs = start_watcher(cfg.root, cfg.hub, on_change=lambda p: say(
        f"changed: {p}"))
    if obs is None:
        say("WARN: watchdog 不可用，变化流无事件")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if obs:
            obs.stop()
        srv.server_close()
    return 0


