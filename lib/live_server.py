"""live_server — 把一个目录当静态网站服务。

spec: .scratch/live-server/spec.md。核心 = stdlib http.server，叠加：
SPA 回退、Basic Auth、PUT 上传（越界 403）、目录美化页、marked.js
Markdown 渲染、SSE 变化流、HTTPS（mkcert/openssl 自签/透传）、
`map` 子命令（OpenLayers 渲染 GeoJSON）。

外部前端依赖（marked.js、OpenLayers）一律「远端 CDN 取 + 本地固定
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


def _load_json_file(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

from lib.notify import is_debug

# CDN 缓存：首次从远端取，落本地固定路径，之后离线可用
CDN_CACHE_ROOT = Path.home() / ".cache" / "lazygophers" / "live-server"
CDN_ASSETS = {
    # 名称 -> 备用远端地址链；serve 于 /_assets/<名称>。
    # ol v10 的 dist 没有 ol.css（样式由 JS 注入），别加回来
    "marked.min.js": (
        "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
        "https://unpkg.com/marked/marked.min.js",
    ),
    "ol.js": (
        "https://cdn.jsdelivr.net/npm/ol@10/dist/ol.js",
        "https://unpkg.com/ol@10/dist/ol.js",
    ),
    "leaflet.js": (
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
    ),
    "leaflet.css": (
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
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
.md-body blockquote{margin:0;padding:0 12px;color:var(--dim);border-left:3px solid var(--line)}
#map{width:100%;height:88vh;border-radius:8px} """

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


# ---------------------------------------------------------------- Leaflet 页面
# 数据只认标准 GeoJSON。要素属性里认三个约定（都可选）：
#   name  名字，悬停显示；面的名字常驻显示，面在屏幕上装不下时自动藏
#   style Leaflet Path 选项（color/weight/fillColor/fillOpacity/dashArray），直接交给 L.geoJSON 的 style
#   floor 楼层（1F/3F/B2）：出一排楼层按钮，带 floor 的只在同层显示，不带的是地面、只在 1F 显示
# 地图文件旁边有同名 .tilejson（TileJSON 3.0.0）就把它当底图，没有就用 OSM。
# 插件都按 Leaflet 标准写法：L.Map.addInitHook 挂上，等主脚本发 geojsonload / tilejsonload 事件。
LEAFLET_CSS = (
    ".ls-label{background:none;border:0;box-shadow:none;padding:0;"
    "font-size:11px;color:#3b3428;text-shadow:0 0 2px #fff,0 0 2px #fff}"
    ".ls-label:before{display:none}"
    ".ls-floors a{width:auto!important;padding:0 7px}"
    ".ls-floors a.on{background:#ffe6a8;font-weight:700}")

LEAFLET_PLUGINS = r"""
// 瓦片缺图时退回上一级瓦片放大（Leaflet.TileLayer.Fallback 的做法）：稀疏金字塔只有局部出到高缩放级
L.TileLayer.Fallback=L.TileLayer.extend({
  createTile:function(c,done){var t=L.TileLayer.prototype.createTile.call(this,c,done);t._c0=c;t._up=0;return t;},
  _tileOnError:function(done,t,e){var c=t._c0,S=this.getTileSize().x;t._up++;
    if(c.z-t._up<(this.options.minNativeZoom||0))return done(e,t);
    var k=1<<t._up,ox=c.x%k,oy=c.y%k;
    // 上一级瓦片放大 k 倍、挪到本格位置，只露出本格那一块
    t.style.width=t.style.height=S*k+'px';t.style.marginLeft=-ox*S+'px';t.style.marginTop=-oy*S+'px';
    t.style.clipPath='inset('+oy*S+'px '+(k-1-ox)*S+'px '+(k-1-oy)*S+'px '+ox*S+'px)';
    t.src=L.Util.template(this._url,L.extend({},this.options,{x:Math.floor(c.x/k),y:Math.floor(c.y/k),z:c.z-t._up,s:'a',r:''}));}});
// 底图：同名 TileJSON
L.Map.addInitHook(function(){var map=this,lay=null,url0='';
  var make=function(tj,base){var b=tj.bounds;url0=/^([a-z]+:)?\/\//.test(tj.tiles[0])?tj.tiles[0]:base+tj.tiles[0];
    return new L.TileLayer.Fallback(url0,{minNativeZoom:tj.minzoom||0,maxNativeZoom:tj.maxzoom||18,maxZoom:24,
      bounds:b?L.latLngBounds([b[1],b[0]],[b[3],b[2]]):undefined,attribution:tj.attribution||''});};
  map.on('tilejsonload',function(e){var tj=e.tilejson;if(!tj||!tj.tiles||!tj.tiles.length)return;
    lay=make(tj,e.base);map.layersControl.addBaseLayer(lay,tj.name||'底图');map.eachLayer(function(l){if(l instanceof L.TileLayer)map.removeLayer(l);});lay.addTo(map);});
  map.on('tilejsonupdate',function(e){var tj=e.tilejson;if(!tj||!tj.tiles||!tj.tiles.length)return;
    var on=lay&&map.hasLayer(lay);if(lay){map.layersControl.removeLayer(lay);map.removeLayer(lay);}
    lay=make(tj,e.base);map.layersControl.addBaseLayer(lay,tj.name||'底图');if(on||!lay)lay.addTo(map);});
  // 瓦片文件变了：网址加版本号绕过浏览器缓存，setUrl 只重画这一层
  map.on('tilesupdate',function(){if(lay)lay.setUrl(url0+(url0.indexOf('?')<0?'?':'&')+'v='+Date.now());});});
// 快捷键：WASD 平移四分之一屏，Q 放大、E 缩小（Z / X 换层在楼层插件里）
L.Map.addInitHook(function(){var map=this;document.addEventListener('keydown',function(e){
  if(e.ctrlKey||e.metaKey||e.altKey||/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)||e.target.isContentEditable)return;
  var k=e.key.toLowerCase(),sz=map.getSize(),mv={w:[0,-sz.y/4],a:[-sz.x/4,0],s:[0,sz.y/4],d:[sz.x/4,0]}[k];
  if(mv)map.panBy(mv);else if(k==='q')map.zoomIn();else if(k==='e')map.zoomOut();else return;e.preventDefault();});});
// 楼层：properties.floor
L.Map.addInitHook(function(){var map=this;map.on('geojsonload',function(ev){
  var g=ev.layer,all=[];g.eachLayer(function(l){all.push(l);});
  var rank=function(n){var b=/^B(\d+)$/.exec(n),f=/^(\d+)F$/.exec(n);return b?-b[1]:f?+f[1]:0;};
  var floors=[],cur='1F',box=null,ctl=null;
  var apply=function(){all.forEach(function(l){var f=l.feature.properties&&l.feature.properties.floor,on=f?f===cur:cur==='1F';
    if(on!==g.hasLayer(l))on?g.addLayer(l):g.removeLayer(l);});
    if(box)Array.prototype.forEach.call(box.children,function(b){b.className=b.textContent===cur?'on':'';});map.fire('floorchange',{floor:cur});};
  var go=function(n){if(n&&n!==cur){cur=n;apply();}};
  var Ctl=L.Control.extend({onAdd:function(){box=L.DomUtil.create('div','leaflet-bar ls-floors');
    floors.forEach(function(n){var b=L.DomUtil.create('a','',box);b.href='#';b.textContent=n;L.DomEvent.on(b,'click',function(e){L.DomEvent.preventDefault(e);go(n);});});
    L.DomEvent.disableClickPropagation(box);return box;}});
  // 楼层表跟着数据走：增删要素后可能多出或少掉一层，按钮条重建，当前层不在了就回 1F
  var rebuild=function(){var set={};all.forEach(function(l){var f=l.feature&&l.feature.properties&&l.feature.properties.floor;if(f)set[f]=1;});
    if(ctl){ctl.remove();ctl=null;box=null;}
    if(!Object.keys(set).length){floors=[];cur='1F';return;}
    set['1F']=1;floors=Object.keys(set).sort(function(a,b){return rank(b)-rank(a);});if(floors.indexOf(cur)<0)cur='1F';
    ctl=new Ctl({position:'topright'}).addTo(map);};
  document.addEventListener('keydown',function(e){if(e.ctrlKey||e.metaKey||e.altKey||/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName))return;
    var k=e.key.toLowerCase();if((k!=='z'&&k!=='x')||!floors.length)return;go(floors[floors.indexOf(cur)+(k==='z'?1:-1)]);e.preventDefault();});
  map.on('geojsonupdate',function(u){var gone=new Set(u.removed);all=all.filter(function(l){return !gone.has(l);}).concat(u.added);rebuild();apply();});
  rebuild();apply();});});
// 面的名字常驻：装不下就藏，放大到装得下再出来
L.Map.addInitHook(function(){var map=this;map.on('geojsonload',function(ev){
  var boxed=[],take=function(l){var p=l.feature&&l.feature.properties;
    if(!p||p.name==null||!l.getBounds||!/Polygon/.test(l.feature.geometry.type))return;
    l.unbindTooltip();l.bindTooltip(String(p.name),{permanent:true,direction:'center',className:'ls-label'});boxed.push(l);};
  ev.layer.eachLayer(take);
  map.on('geojsonupdate',function(u){var gone=new Set(u.removed);boxed=boxed.filter(function(l){return !gone.has(l);});u.added.forEach(take);fit();});
  var fit=function(){boxed.forEach(function(l){if(!l._map)return;var b=l.getBounds(),p1=map.latLngToContainerPoint(b.getNorthWest()),p2=map.latLngToContainerPoint(b.getSouthEast());
    var ok=Math.abs(p2.x-p1.x)>String(l.feature.properties.name).length*12+6&&Math.abs(p2.y-p1.y)>14;ok?l.openTooltip():l.closeTooltip();});};
  map.on('zoomend moveend floorchange',fit);fit();});});
"""

LEAFLET_MAIN = r"""
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]);
const getData=()=>fetch('/data.geojson',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});
const getTj=()=>fetch('/_map/'+encodeURIComponent(STEM)+'.tilejson',{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null);
Promise.all([getData(),getTj()]).then(([gj,tj])=>{
  const n=(gj.features||[]).length,map=L.map('map',{maxZoom:24,preferCanvas:n>2000});
  const osm=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxNativeZoom:19,maxZoom:24,attribution:'&copy; OpenStreetMap'}).addTo(map);
  map.layersControl=L.control.layers({'OSM':osm},{},{collapsed:true}).addTo(map);
  map.fire('tilejsonload',{tilejson:tj,base:'/_map/'});
  const st=f=>(f&&f.properties&&f.properties.style)||{};
  let made=[];
  const layer=L.geoJSON(null,{style:st,pointToLayer:(f,ll)=>L.circleMarker(ll,Object.assign({radius:4},st(f))),
    onEachFeature:(f,l)=>{const p=f.properties||{};if(p.name!=null)l.bindTooltip(esc(p.name));
      l.bindPopup('<pre>'+esc(JSON.stringify(p,(k,v)=>k==='style'?undefined:v,2))+'</pre>');made.push(l);}}).addTo(map);
  // 每条要素按整条 JSON 记一个键：文件变了只增删变过的要素，没变的原样留在图上
  // 同一条 JSON 可能出现多次（重复要素），键下挂一个图层数组
  const keyed=new Map();
  const add=fs=>{const out=[];fs.forEach(f=>{made=[];layer.addData(f);const k=JSON.stringify(f);
    if(!keyed.has(k))keyed.set(k,[]);keyed.get(k).push(made[0]||null);if(made[0])out.push(made[0]);});return out;};
  add(gj.features||[]);
  map.layersControl.addOverlay(layer,'数据');
  try{map.fitBounds(layer.getBounds(),{padding:[20,20]});}catch(e){map.setView([0,0],2);}
  map.fire('geojsonload',{layer:layer,data:gj});
  const sync=()=>getData().then(g=>{
    const fs=g.features||[],want=new Map(),removed=[],fresh=[];
    fs.forEach(f=>{const k=JSON.stringify(f);want.set(k,(want.get(k)||0)+1);});
    // 多出来的份数删掉，缺的份数补上
    keyed.forEach((ls,k)=>{const keep=want.get(k)||0;while(ls.length>keep){const l=ls.pop();if(l){layer.removeLayer(l);removed.push(l);}}if(!ls.length)keyed.delete(k);});
    fs.forEach(f=>{const k=JSON.stringify(f),have=(keyed.get(k)||[]).length,need=want.get(k);if(have<need){want.set(k,need-1);fresh.push(f);}else want.set(k,need);});
    const added=add(fresh);
    if(added.length||removed.length)map.fire('geojsonupdate',{layer:layer,data:g,added:added,removed:removed});
  }).catch(err=>console.warn('增量刷新失败',err));
  // 监听 live_server 的变化流：地图文件变了只换变过的要素；TileJSON / 瓦片变了只重画底图。视图、楼层、底图选择都不动
  const wait={},later=(k,ms,fn)=>{clearTimeout(wait[k]);wait[k]=setTimeout(fn,ms);};
  const es=new EventSource('/__changes/stream');
  es.addEventListener('change',e=>{const p=JSON.parse(e.data).path;
    if(p===FILE)later('data',250,sync);
    else if(p===STEM+'.tilejson')later('tj',250,()=>getTj().then(t=>map.fire('tilejsonupdate',{tilejson:t,base:'/_map/'})));
    else if(/^tiles\//.test(p))later('tiles',800,()=>map.fire('tilesupdate'));});
}).catch(err=>{document.getElementById('map').textContent='加载失败: '+err.message;});
"""


def render_map_page(title: str, lib: str = "ol") -> str:
    """OpenLayers（默认）或 Leaflet 渲染 /data.geojson；两者都吃规范 GeoJSON。"""
    # 地图页铺满整个窗口：去掉 main 的居中版心和内边距，地图容器固定占满视口
    head = ('<style>main{max-width:none;margin:0;padding:0}'
            '#map{position:fixed;inset:0;width:100vw;height:100vh;border-radius:0}</style>'
            '<div id="map"></div>')
    if lib == "leaflet":
        stem = json.dumps(Path(title).stem)
        body = (head
            + '<link rel="stylesheet" href="/_assets/leaflet.css">'
            + '<style>' + LEAFLET_CSS + '</style>'
            + '<script src="/_assets/leaflet.js"></script>'
            + '<script>' + LEAFLET_PLUGINS + '</script>'
            + '<script>const STEM=' + stem + ',FILE=' + json.dumps(title) + ';'
            + LEAFLET_MAIN + '</script>')
        return _page(title, body)
    body = (head
            + '<script src="/_assets/ol.js"></script>'
            + '<script>fetch("/data.geojson").then(r=>{if(!r.ok)throw new Error('
            '"HTTP "+r.status);return r.json()}).then(gj=>{'
            'const fmt=new ol.format.GeoJSON();'
            'const vector=new ol.layer.Vector({source:new ol.source.Vector({'
            'features:fmt.readFeatures(gj,{featureProjection:"EPSG:3857"})})});'
            'const map=new ol.Map({target:"map",layers:['
            'new ol.layer.Tile({source:new ol.source.OSM()}),vector],'
            'view:new ol.View({center:ol.proj.fromLonLat([116.4,39.9]),zoom:4})});'
            'map.on("click",e=>{const f=map.forEachFeatureAtPixel(e.pixel,'
            'f=>f);const el=document.getElementById("map");'
            'el.title=f?JSON.stringify(f.getProperties(),(k,v)=>k==="geometry"?undefined:v):"";});'
            '}).catch(err=>{document.getElementById("map").textContent='
            '"加载失败: "+err.message;});</script>')
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
    map_file: Path | None = None
    render_lib: str = "ol"
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

            if cfg.map_file:  # map 子命令：单页 + 数据端点
                if path == "/":
                    return self._reply_html(
                        200, render_map_page(cfg.map_file.name,
                                             lib=cfg.render_lib))
                if path == "/data.geojson":
                    data = _load_json_file(cfg.map_file)
                    if cfg.render_lib == "leaflet":
                        data = normalize_geojson(data)
                    return self._reply(
                        200, json.dumps(data, ensure_ascii=False).encode(),
                        "application/geo+json")
                if path.startswith("/_map/"):  # 地图文件同目录的旁挂文件：TileJSON、瓦片
                    target = self._resolve(path[len("/_map/"):])
                    if target is None:
                        return self._reply(403, b"forbidden", "text/plain")
                    if target.is_file():
                        ctype = mimetypes.guess_type(target.name)[0] or \
                            "application/octet-stream"
                        return self._reply(200, target.read_bytes(), ctype)
                return self._reply(404, b"not found", "text/plain")

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
    if cfg.open_browser and not cfg.map_file:
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


# ---------------------------------------------------------------- GeoJSON lint
_POSITION_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString",
                   "Polygon", "MultiPolygon"}
_GEOMETRY_TYPES = _POSITION_TYPES | {"GeometryCollection"}


def _check_position(pos, at: str, errors: list[str]) -> bool:
    if (not isinstance(pos, list) or len(pos) < 2
            or not all(isinstance(n, (int, float)) and not isinstance(n, bool)
                       for n in pos)):
        errors.append(f"{at}: 位置必须是 [数字, 数字(, 高程)]，实际 "
                      f"{json.dumps(pos, ensure_ascii=False)}")
        return False
    return True


def _check_geometry(geom, at: str, errors: list[str]) -> None:
    if geom is None:  # null geometry 合法（RFC 7946 §3.1）
        return
    if not isinstance(geom, dict) or "type" not in geom:
        errors.append(f"{at}: 缺 type 或不是对象")
        return
    gtype = geom["type"]
    if gtype == "GeometryCollection":
        geoms = geom.get("geometries")
        if not isinstance(geoms, list):
            errors.append(f"{at}.geometries: 必须是数组")
            return
        for i, g in enumerate(geoms):
            _check_geometry(g, f"{at}.geometries[{i}]", errors)
        return
    if gtype not in _POSITION_TYPES:
        errors.append(f"{at}.type: 未知几何类型 {gtype!r}")
        return
    coords = geom.get("coordinates")
    label = f"{at}.coordinates"

    def position(p, a):
        return _check_position(p, a, errors)

    def ring(r, a):
        if (not isinstance(r, list) or len(r) < 4
                or r[0] != r[-1]):
            errors.append(f"{a}: 线环必须 ≥4 个位置且首尾相同（闭合）")
            return
        for i, p in enumerate(r):
            position(p, f"{a}[{i}]")

    def line(ls, a, minimum=2):
        if not isinstance(ls, list) or len(ls) < minimum:
            errors.append(f"{a}: 至少 {minimum} 个位置")
            return
        for i, p in enumerate(ls):
            position(p, f"{a}[{i}]")

    if gtype == "Point":
        position(coords, label)
    elif gtype == "MultiPoint":
        line(coords, label, minimum=1)
    elif gtype == "LineString":
        line(coords, label)
    elif gtype == "MultiLineString":
        if not isinstance(coords, list):
            errors.append(f"{label}: 必须是数组")
            return
        for i, ls in enumerate(coords):
            line(ls, f"{label}[{i}]")
    elif gtype == "Polygon":
        if not isinstance(coords, list):
            errors.append(f"{label}: 必须是数组")
            return
        for i, r in enumerate(coords):
            ring(r, f"{label}[{i}]")
    elif gtype == "MultiPolygon":
        if not isinstance(coords, list):
            errors.append(f"{label}: 必须是数组")
            return
        for i, poly in enumerate(coords):
            if not isinstance(poly, list):
                errors.append(f"{label}[{i}]: 必须是线环数组")
                continue
            for j, r in enumerate(poly):
                ring(r, f"{label}[{i}][{j}]")


def geojson_errors(data) -> list[str]:
    """按 RFC 7946 校验 GeoJSON，返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["根不是 JSON 对象"]
    rtype = data.get("type")
    if not rtype:
        return ['根缺 "type" 字段（OpenLayers 报 "Unsupported GeoJSON type: '
                'undefined" 就是这个）']
    if "crs" in data:
        errors.append("根.crs: RFC 7946 已废除 crs 字段，一律按 WGS84；"
                      "OpenLayers 会忽略它")
    bbox = data.get("bbox")
    if bbox is not None and (not isinstance(bbox, list)
                             or len(bbox) % 2
                             or not all(isinstance(n, (int, float))
                                        for n in bbox)):
        errors.append("根.bbox: 必须是偶数长度的数字数组")

    def feature(f, at):
        if not isinstance(f, dict):
            errors.append(f"{at}: 必须是对象")
            return
        if f.get("type") != "Feature":
            errors.append(f'{at}.type: 必须是 "Feature"，实际 '
                          f'{f.get("type")!r}')
        if "id" not in f and "geometry" not in f and "properties" not in f:
            errors.append(f"{at}: 没有 geometry 也没有 properties，疑似非要素对象")
            return
        _check_geometry(f.get("geometry"), f"{at}.geometry", errors)
        props = f.get("properties")
        if props is not None and not isinstance(props, dict):
            errors.append(f"{at}.properties: 必须是对象或 null")

    if rtype == "FeatureCollection":
        feats = data.get("features")
        if not isinstance(feats, list):
            errors.append('根.features: FeatureCollection 必须有 features 数组')
        else:
            for i, f in enumerate(feats):
                feature(f, f"features[{i}]")
    elif rtype == "Feature":
        feature(data, "根")
    elif rtype in _GEOMETRY_TYPES:
        _check_geometry(data, "根", errors)
    else:
        errors.append(f"根.type: 未知类型 {rtype!r}")
    return errors


def normalize_geojson(data):
    """宽容归一化：要素数组/裸几何 → FeatureCollection。

    Leaflet 的 L.geoJSON 两样都能吃，OpenLayers 不行；--leaflet 模式
    在 /data.geojson 出口统一归一，页面端永远拿到规范形状。
    """
    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}
    if isinstance(data, dict) and data.get("type") in _GEOMETRY_TYPES:
        return {"type": "Feature", "geometry": data, "properties": None}
    return data

