"""websearch — 多引擎网页检索,输出 标题 / URL / 摘要。

引擎链:DuckDuckGo HTML → DuckDuckGo Lite → Bing,依次尝试直到取够 N 条
(全部免 key)。curl_cffi 指纹直抓(与 webgrab 同源)。结果按 URL 去重。
结尾提示用 `webgrab <url>` 抓正文。
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, unquote, urlparse, quote_plus

EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class SearchError(RuntimeError):
    pass


def _fetch(url: str, timeout: float) -> str:
    """curl_cffi 指纹直抓,返回 HTML。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get(url, headers=EXTRA_HEADERS, allow_redirects=True)
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return r.text


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


# 引擎链:依次尝试直到取够 limit 条。全挂才落到下一个。
ENGINES: list[tuple[str, str, object]] = [
    ("ddg", "https://html.duckduckgo.com/html/?q=", parse_ddg),
    ("ddg-lite", "https://lite.duckduckgo.com/lite/?q=", parse_ddg_lite),
    ("bing", "https://www.bing.com/search?q=", parse_bing),
]


def search(query: str, limit: int = 10, engine: str | None = None,
           timeout: float = 15) -> list[dict]:
    """检索,返回去重后的 [{url, title, snippet}],最多 limit 条。"""
    chain = [(n, u, p) for n, u, p in ENGINES if not engine or n == engine]
    if not chain:
        raise SearchError(f"未知引擎: {engine}(可选: {', '.join(n for n, _, _ in ENGINES)})")
    seen, results, errors = set(), [], []
    for name, base, parser in chain:
        try:
            items = parser(_fetch(base + quote_plus(query), timeout))
        except Exception as e:  # 网络错 / 反爬拦 / 解析空都换下一个引擎
            errors.append(f"{name}: {e}")
            continue
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            results.append(it)
            if len(results) >= limit:
                return results
        if items:
            print(f"[websearch] {name} 只返回 {len(items)} 条,不够 {limit},换下一个引擎", file=sys.stderr)
    if not results:
        raise SearchError("所有引擎都没有结果: " + "; ".join(errors or ["解析到 0 条"]))
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="websearch",
        description="多引擎网页检索(DuckDuckGo → Bing 链式,免 key),输出 标题 / URL / 摘要",
        epilog="结果只有摘要,要看正文用: webgrab <url>",
    )
    p.add_argument("query", nargs="+", help="搜索词(多词直接跟在后面)")
    p.add_argument("-n", "--limit", type=int, default=10, help="最多返回几条(默认 10)")
    p.add_argument("--engine", choices=[n for n, _, _ in ENGINES], help="只用指定引擎(默认链式全试)")
    p.add_argument("--json", action="store_true", help="输出 JSON(管道给 jq 用)")
    p.add_argument("--timeout", type=float, default=15, help="单引擎超时秒数(默认 15)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv[1:] if argv is not None else None)
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
