"""websearch — 多引擎网页检索,输出 标题 / URL / 摘要。

引擎全部免 key:DDG / DDG-lite / Bing / Google / Yandex / 搜狗 / 360 爬网页,
GitHub 爬仓库搜索页,Wikipedia / arXiv / Crossref / PubMed 用官方免 key API
(wikipedia zh 查空回退 en)。并行查询后按 URL 合并去重。curl_cffi 指纹直抓
(与 webgrab 同源),单引擎被拦或失败只跳过该引擎,不影响其他。结尾提示用
`webgrab <url>` 抓正文。
"""

from __future__ import annotations

import argparse
import base64
import sys
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

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


def _unwrap_bing(href: str) -> str:
    """Bing 结果链接是 bing.com/ck/a?...&u=a1<base64>,解出真 URL。"""
    parsed = urlparse(href)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
        u = parse_qs(parsed.query).get("u", [""])[0]
        if u.startswith("a1"):
            body = u[2:]
            body += "=" * (-len(body) % 4)
            try:
                real = base64.urlsafe_b64decode(body).decode("utf-8", "replace")
            except ValueError:
                return href
            if real.startswith("http"):
                return real
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
        url = _unwrap_bing(a["href"])
        if not url.startswith("http"):
            continue
        cap = li.select_one(".b_caption p, .b_caption")
        out.append({"url": url, "title": _text(a), "snippet": _text(cap)})
    return out


def parse_google(html: str) -> list[dict]:
    """解析 Google 结果页(含 h3 标题的非站内链接)。

    Google 的结果容器 class 常变,锚点是「<a href> 里套 <h3>」这个稳定结构;
    摘要类名 VwiC3b 同样不稳,取不到就空,不猜。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for h3 in soup.find_all("h3"):
        a = h3.find_parent("a", href=True)
        if not a:
            continue
        parsed = urlparse(a["href"])
        host = parsed.netloc.lower()
        if not host or host.endswith("google.com"):
            continue
        # 摘要:结果容器(a 的祖先 div)里的文本块,标题除外
        snippet = ""
        container = a.find_parent("div")
        if container:
            for div in container.find_all("div", recursive=False):
                txt = _text(div)
                if txt and txt != _text(h3):
                    snippet = txt
                    break
        out.append({"url": a["href"], "title": _text(h3), "snippet": snippet})
    return out


def parse_yandex(html: str) -> list[dict]:
    """解析 Yandex 结果页(li.serp-item;链接已是真 URL 不带跳转)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.serp-item"):
        a = li.select_one("a.Link[href]")
        if not a or not a.get("href", "").startswith("http"):
            continue
        h2 = li.find("h2")
        sn = li.select_one(".OrganicTextContentSpan, .TextContainer, "
                           ".organic__content-wrapper")
        out.append({"url": a["href"], "title": _text(h2) or _text(a),
                    "snippet": _text(sn)})
    return out


def parse_github(html: str) -> list[dict]:
    """解析 GitHub 仓库搜索页(div[data-testid=results-list] 下的条目)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    lst = soup.select_one("div[data-testid='results-list']")
    for item in (lst.select(":scope > div") if lst else []):
        a = item.select_one("div.search-title a[href]")
        if not a:
            continue
        href = a["href"]
        url = ("https://github.com" + href) if href.startswith("/") else href
        title = _text(a)
        # 候选摘要里第一个与标题不同的(第一个 search-match 是高亮重复的仓库名)
        snippet = ""
        for cand in item.select("span.line-clamp-2, .search-match"):
            txt = _text(cand)
            if txt and txt != title:
                snippet = txt
                break
        out.append({"url": url, "title": title, "snippet": snippet})
    return out


def parse_wikipedia(data: dict, lang: str = "zh") -> list[dict]:
    """解析 Wikipedia query=search API 响应(search[].title/snippet)。"""
    import re

    wiki = data.get("query", {}).get("search", [])
    out = []
    for hit in wiki:
        title = hit.get("title", "")
        # snippet 是 HTML 片段,去标签
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", "")).strip()
        out.append({"url": f"https://{lang}.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_")),
                    "title": title, "snippet": snippet})
    return out


def parse_sogou(html: str) -> list[dict]:
    """解析搜狗结果页(vrwrap/rb 块;知乎类结果只有 /link 跳转,由引擎函数解真 URL)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for b in soup.select("div.vrwrap, div.rb"):
        a = b.select_one("h3 a[href]")
        if not a:
            continue
        sn = b.select_one(".text-layout")
        out.append({"url": a["href"], "title": _text(a), "snippet": _text(sn)})
    return out


def parse_360(html: str) -> list[dict]:
    """解析 360 搜索结果页(li.res-list;真 URL 在 h3 a 的 data-mdurl 属性)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.res-list"):
        a = li.select_one("h3 a")
        if not a:
            continue
        url = a.get("data-mdurl") or a.get("href") or ""
        if not url.startswith("http"):
            continue
        # href 会退到会话跳转链 so.com/link?m=...,只认 data-mdurl
        if url.startswith("https://www.so.com/link"):
            continue
        sn = li.select_one("div.res-rich")
        out.append({"url": url, "title": _text(a), "snippet": _text(sn)})
    return out


def parse_arxiv(xml: str) -> list[dict]:
    """解析 arXiv API 的 Atom 响应(entry[].title/summary/id)。"""
    import re

    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        def tag(name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", entry, re.S)
            return " ".join(m.group(1).split()) if m else ""
        url = tag("id")
        if url.startswith("http"):
            out.append({"url": url, "title": tag("title"), "snippet": tag("summary")[:300]})
    return out


def parse_crossref(data: dict) -> list[dict]:
    """解析 Crossref works 响应(items[].title/URL/abstract)。"""
    import re

    out = []
    for it in data.get("message", {}).get("items", []):
        title = (it.get("title") or [""])[0]
        url = it.get("URL", "")
        # abstract 是 JATS XML 片段,去标签
        abstract = re.sub(r"<[^>]+>", "", it.get("abstract", "")).strip()
        if url.startswith("http"):
            out.append({"url": url, "title": title, "snippet": abstract[:300]})
    return out


# 引擎定义:(名称, 取结果函数名, 签名 (query, timeout))。
# 存函数名而非引用,调用时经模块属性解析 —— 方便测试 mock.patch。
# 单引擎失败(被拦/网络)只跳过,不影响其余引擎;并行查询。
def _e_ddg(query, timeout):
    return parse_ddg(_fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout))


def _e_ddg_lite(query, timeout):
    return parse_ddg_lite(_fetch("https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), timeout))


def _e_bing(query, timeout):
    return parse_bing(_fetch("https://www.bing.com/search?q=" + quote_plus(query), timeout))


def _e_google(query, timeout):
    # 注意: Google 按出口 IP 风控,被标记的 IP 会拿到「请启用 JS」/ reCAPTCHA
    # 中间页(解析为 0 条,自动跳过该引擎);渲染也过不了 reCAPTCHA,不做回退
    return parse_google(_fetch("https://www.google.com/search?q=" + quote_plus(query), timeout))


def _e_yandex(query, timeout):
    return parse_yandex(_fetch("https://yandex.com/search/?text=" + quote_plus(query), timeout))


def _e_github(query, timeout):
    return parse_github(_fetch("https://github.com/search?type=repositories&q=" + quote_plus(query),
                               timeout))


def _e_wikipedia(query, timeout):
    """Wikipedia 免 key 官方 API;zh 查空回退 en。"""
    from curl_cffi import requests

    params = {"action": "query", "list": "search", "format": "json", "srlimit": 5}
    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        for lang in ("zh", "en"):
            r = s.get(f"https://{lang}.wikipedia.org/w/api.php",
                      params={**params, "srsearch": query}, headers=EXTRA_HEADERS)
            if r.status_code != 200:
                raise SearchError(f"HTTP {r.status_code}")
            items = parse_wikipedia(r.json(), lang)
            if items:
                return items
    return []


def _e_sogou(query, timeout):
    """搜狗;结果页部分链接是 /link 跳转,GET 一次解 meta refresh 里的真 URL。"""
    import re

    from curl_cffi import requests

    items = parse_sogou(_fetch(
        "https://www.sogou.com/web?query=" + quote_plus(query), timeout))
    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        for it in items:
            if it["url"].startswith("/link"):
                link = "https://www.sogou.com" + it["url"]
                r = s.get(link, allow_redirects=False, headers={"Referer": "https://www.sogou.com/"})
                # 跳转页是 meta refresh: URL='真链接'
                m = re.search(r"URL='?([^'\">]+)", r.text[:500])
                real = r.headers.get("Location") or (m.group(1) if m else "")
                if real.startswith("http"):
                    it["url"] = real
    return [it for it in items if it["url"].startswith("http")]


def _e_360(query, timeout):
    return parse_360(_fetch("https://www.so.com/s?q=" + quote_plus(query), timeout))


def _e_arxiv(query, timeout):
    """arXiv 官方免 key API(Atom XML)。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get("https://export.arxiv.org/api/query",
                  params={"search_query": "all:" + query, "max_results": 5})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return parse_arxiv(r.text)


def _e_crossref(query, timeout):
    """Crossref 官方免 key API(跨库论文 DOI 元数据)。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get("https://api.crossref.org/works",
                  params={"query": query, "rows": 5,
                          "select": "title,URL,abstract"})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return parse_crossref(r.json())


def _e_pubmed(query, timeout):
    """PubMed 官方免 key API(esearch 拿 id → esummary 拿标题)。

    eutils 对 chrome TLS 指纹直接 reset,这里用朴素请求。
    """
    from curl_cffi import requests

    with requests.Session(timeout=timeout) as s:
        r = s.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                  params={"db": "pubmed", "term": query, "retmode": "json", "retmax": 5})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        r = s.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                  params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        out = []
        for pid in ids:
            item = r.json().get("result", {}).get(pid, {})
            title = " ".join(item.get("title", "").split())
            if title:
                out.append({"url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                            "title": title.rstrip("."),
                            "snippet": item.get("source", "")})
        return out


ENGINES: list[tuple[str, str]] = [
    ("ddg", "_e_ddg"),
    ("ddg-lite", "_e_ddg_lite"),
    ("bing", "_e_bing"),
    ("google", "_e_google"),
    ("yandex", "_e_yandex"),
    ("sogou", "_e_sogou"),
    ("360", "_e_360"),
    ("arxiv", "_e_arxiv"),
    ("crossref", "_e_crossref"),
    ("pubmed", "_e_pubmed"),
    ("wikipedia", "_e_wikipedia"),
    ("github", "_e_github"),
]


def search(query: str, limit: int = 10, engine: str | None = None,
           timeout: float = 15) -> list[dict]:
    """并行检索所有引擎,按 URL 合并去重,返回最多 limit 条(首见顺序保留)。"""
    from concurrent.futures import ThreadPoolExecutor

    mod = sys.modules[__name__]
    chain = [(n, getattr(mod, fn)) for n, fn in ENGINES if not engine or n == engine]
    if not chain:
        raise SearchError(f"未知引擎: {engine}(可选: {', '.join(n for n, _ in ENGINES)})")

    def _run(nf):
        name, fn = nf
        try:
            return name, fn(query, timeout), ""
        except Exception as e:  # 网络错 / 反爬拦,该引擎记错继续
            return name, [], str(e)

    with ThreadPoolExecutor(max_workers=len(chain)) as ex:
        outcomes = dict(zip((n for n, _ in chain), ex.map(_run, chain)))
    seen, results, errors = set(), [], []
    for name, _fn in chain:  # 按 ENGINES 静态序合并,顺序稳定可测
        _, items, err = outcomes[name]
        if err:
            errors.append(f"{name}: {err}")
            continue
        print(f"[websearch] {name} 返回 {len(items)} 条", file=sys.stderr)
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"])
                results.append(it)
    if not results:
        raise SearchError("所有引擎都没有结果: " + "; ".join(errors or ["解析到 0 条"]))
    return results[:limit]


def list_engines() -> int:
    """打印全部引擎。"""
    for i, (name, _fn) in enumerate(ENGINES, 1):
        print(f"{i}. {name}")
    print("[websearch] 默认全部查询后按 URL 合并,--engine <名称> 可指定单个", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="websearch",
        description="多引擎网页检索(DDG/Bing/Google/Yandex/GitHub/Wikipedia 全免 key,并行+按 URL 合并去重,含 arXiv/Crossref/PubMed 学术源),输出 标题 / URL / 摘要",
        epilog="结果只有摘要,要看正文用: webgrab <url>\n"
               "列出引擎: websearch engines",
    )
    p.add_argument("query", nargs="+", help="搜索词(多词直接跟在后面)")
    p.add_argument("-n", "--limit", type=int, default=10, help="最多返回几条(默认 10)")
    p.add_argument("--engine", choices=[n for n, _ in ENGINES],
                   help="只用指定引擎(默认全部引擎)")
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
    # engines 子命令直接列出引擎,不进 argparse
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
