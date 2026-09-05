"""websearch — 多引擎网页检索,输出 标题 / URL / 摘要。

免 key 引擎:DuckDuckGo HTML / DuckDuckGo Lite / Bing 网页版。
API-key 引擎:Google Custom Search(key+cx)/ Brave Search(key),配置在
~/.config/lazygophers/scripts/websearch.yaml,配了自动启用并排前面
(去重时优先保留)。
全部查询后按 URL 合并去重。curl_cffi 指纹直抓(与 webgrab 同源)。
结尾提示用 `webgrab <url>` 抓正文。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

CONFIG_PATH = Path(
    os.environ.get("WEBSEARCH_CONFIG")
    or Path.home() / ".config/lazygophers/scripts/websearch.yaml"
)


class SearchError(RuntimeError):
    pass


def load_keys() -> dict:
    """读 key 配置,文件不存在返回空 dict。"""
    import yaml

    try:
        data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _fetch(url: str, timeout: float) -> str:
    """curl_cffi 指纹直抓,返回 HTML。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get(url, headers=EXTRA_HEADERS, allow_redirects=True)
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return r.text


def _fetch_json(url: str, params: dict, timeout: float, headers: dict | None = None) -> dict:
    """curl_cffi GET JSON,非 200 时带服务端错误消息抛出。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get(url, params=params, headers=headers or {})
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise SearchError(f"HTTP {r.status_code} {msg}".strip())
        return r.json()


def _unwrap_ddg(href: str) -> str:
    """DDG 结果链接是 //duckduckgo.com/l/?uddg=<编码真链接>,解包出真 URL。"""
    if href.startswith("//"):
        href = "https:" + href
    if urlparse(href).path == "/l/":
        uddg = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _text(node) -> str:
    """节点文本:压空白、去零宽字符。"""
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_ddg(html: str) -> list[dict]:
    """解析 DuckDuckGo HTML 版结果页。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one(".result__a")
        if not a or not a.get("href"):
            continue
        url = _unwrap_ddg(a["href"])
        if not url.startswith("http"):
            continue
        snippet_node = res.select_one(".result__snippet")
        out.append({"url": url, "title": _text(a), "snippet": _text(snippet_node)})
    return out


def parse_ddg_lite(html: str) -> list[dict]:
    """解析 DuckDuckGo Lite 版结果页(纯 table 布局:链接行/摘要行交替)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.result-link"):
        url = _unwrap_ddg(a.get("href", ""))
        if not url.startswith("http"):
            continue
        # 摘要在链接所在行的下下个 <tr>(中间隔一个「更多结果」行)
        snippet = ""
        row = a.find_parent("tr")
        if row:
            nxt = row.find_next_sibling("tr")
            if nxt:
                nxt2 = nxt.find_next_sibling("tr")
                snippet = _text(nxt2.find("td")) if nxt2 else ""
        out.append({"url": url, "title": _text(a), "snippet": snippet})
    return out


def parse_bing(html: str) -> list[dict]:
    """解析 Bing 结果页(li.b_algo)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        url = a["href"]
        if not url.startswith("http"):
            continue
        cap = li.select_one(".b_caption p, .b_caption")
        out.append({"url": url, "title": _text(a), "snippet": _text(cap)})
    return out


def parse_google(data: dict) -> list[dict]:
    """解析 Google Custom Search JSON API 响应(items[].link/title/snippet)。"""
    return [
        {"url": i.get("link", ""), "title": i.get("title", ""), "snippet": i.get("snippet", "")}
        for i in data.get("items", [])
        if i.get("link")
    ]


def parse_brave(data: dict) -> list[dict]:
    """解析 Brave Search API 响应(web.results[].url/title/description)。"""
    results = (data.get("web") or {}).get("results") or []
    return [
        {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")}
        for r in results
        if r.get("url")
    ]


# 引擎定义:(名称, 取结果函数名(签名 (query, timeout, cfg)), 是否需要 key)。
# 存函数名而非引用,调用时经模块属性解析 —— 方便测试 mock.patch。
# key 引擎配置了才参与;参与时排免 key 引擎前面(质量优先,去重先保留)。
# ponytail: 引擎顺序 = 静态声明序,6 个以上再抽 priority 字段
def _e_ddg(query, timeout, cfg):
    return parse_ddg(_fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout))


def _e_ddg_lite(query, timeout, cfg):
    return parse_ddg_lite(_fetch("https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), timeout))


def _e_bing(query, timeout, cfg):
    return parse_bing(_fetch("https://www.bing.com/search?q=" + quote_plus(query), timeout))


def _e_google(query, timeout, cfg):
    data = _fetch_json("https://www.googleapis.com/customsearch/v1", {
        "key": cfg["api_key"], "cx": cfg["cx"], "q": query, "num": 10,
    }, timeout)
    return parse_google(data)


def _e_brave(query, timeout, cfg):
    data = _fetch_json("https://api.search.brave.com/res/v1/web/search",
                       {"q": query, "count": 10}, timeout,
                       {"X-Subscription-Token": cfg["api_key"], "Accept": "application/json"})
    return parse_brave(data)


ENGINES: list[tuple[str, str, bool]] = [
    ("google", "_e_google", True),
    ("brave", "_e_brave", True),
    ("ddg", "_e_ddg", False),
    ("ddg-lite", "_e_ddg_lite", False),
    ("bing", "_e_bing", False),
]

# Google Custom Search 官方要求 cx;Brave 只要 api_key
_ENGINE_KEYS: dict[str, tuple[str, ...]] = {
    "google": ("api_key", "cx"),
    "brave": ("api_key",),
}


def _ready(cfg: dict, name: str) -> bool:
    """key 引擎配置齐了没(google 要 api_key+cx,brave 要 api_key)。"""
    return all(cfg.get(k) for k in _ENGINE_KEYS.get(name, ()))


def search(query: str, limit: int = 10, engine: str | None = None,
           timeout: float = 15) -> list[dict]:
    """检索所有参与引擎并按 URL 合并去重,返回最多 limit 条(首见顺序保留)。

    key 引擎配好 key 才参与;--engine 指定未配置的 key 引擎时报错。
    """
    keys = load_keys()
    mod = sys.modules[__name__]
    chain = [(n, getattr(mod, fn)) for n, fn, _keyed in ENGINES if not engine or n == engine]
    if not chain:
        raise SearchError(f"未知引擎: {engine}(可选: {', '.join(n for n, _, _ in ENGINES)})")
    seen, results, errors = set(), [], []
    for name, fn in chain:
        cfg = keys.get(name) or {}
        if name in _ENGINE_KEYS and not _ready(cfg, name):
            if engine == name:
                raise SearchError(
                    f"引擎 {name} 未配置 key,在 {CONFIG_PATH} 填 {list(_ENGINE_KEYS[name])}")
            continue
        try:
            items = fn(query, timeout, cfg)
        except Exception as e:  # 网络错 / 反爬拦 / key 无效,跳过该引擎继续合并其他
            errors.append(f"{name}: {e}")
            continue
        print(f"[websearch] {name} 返回 {len(items)} 条", file=sys.stderr)
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"])
                results.append(it)
    if not results:
        raise SearchError("所有引擎都没有结果: " + "; ".join(errors or ["解析到 0 条"]))
    return results[:limit]


def _mask(v: str) -> str:
    """key 预览:前 4 位 + ***。"""
    return (v[:4] + "***") if v else "(空)"


def list_engines() -> int:
    """打印全部引擎 + key 配置状态。"""
    keys = load_keys()
    print(f"配置文件: {CONFIG_PATH}")
    for i, (name, _fn, keyed) in enumerate(ENGINES, 1):
        if not keyed:
            print(f"{i}. {name}  免key")
        else:
            cfg = keys.get(name) or {}
            fields = ", ".join(
                f"{k}={_mask(str(cfg.get(k) or ''))}" for k in _ENGINE_KEYS[name]
            )
            state = "已配置" if _ready(cfg, name) else "未配置(跳过)"
            print(f"{i}. {name}  需key: {fields}  {state}")
    print("[websearch] 已配置的 key 引擎自动参与并排前面,--engine <名称> 可指定单个", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="websearch",
        description="多引擎网页检索(全部参与引擎都查,按 URL 合并去重),输出 标题 / URL / 摘要",
        epilog="结果只有摘要,要看正文用: webgrab <url>\n"
               "列出引擎与 key 状态: websearch engines",
    )
    p.add_argument("query", nargs="+", help="搜索词(多词直接跟在后面)")
    p.add_argument("-n", "--limit", type=int, default=10, help="最多返回几条(默认 10)")
    p.add_argument("--engine", choices=[n for n, _, _ in ENGINES],
                   help="只用指定引擎(默认全部参与引擎)")
    p.add_argument("--json", action="store_true", help="输出 JSON(管道给 jq 用)")
    p.add_argument("--timeout", type=float, default=15, help="单引擎超时秒数(默认 15)")
    return p


def main(argv: list[str] | None = None) -> int:
    rest = argv[1:] if argv is not None else None
    if not rest:
        # 裸跑默认打 AI 向 skills 说明(--skills 同款)
        from lib.skills_help import command_name, render_skills
        print(render_skills(command_name(argv[0]), __doc__))
        return 0
    # engines 子命令直接列出引擎与配置状态,不进 argparse
    if rest[0] == "engines":
        return list_engines()
    args = build_parser().parse_args(rest)
    try:
        results = search(" ".join(args.query), limit=args.limit,
                         engine=args.engine, timeout=args.timeout)
    except SearchError as e:
        print(f"[websearch] 检索失败: {e}", file=sys.stderr)
        return 1
    if args.json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['title']}")
        print(f"   {r['url']}")
        if r["snippet"]:
            print(f"   {r['snippet']}")
    print("[websearch] 看正文: webgrab <url>", file=sys.stderr)
    return 0
