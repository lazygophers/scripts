"""websearch — 多引擎网页检索,输出 标题 / URL / 摘要。

引擎全部免 key:DDG / DDG-lite / Bing / Google / Yandex / 搜狗 / 360 爬网页,
GitHub 爬仓库搜索页,Wikipedia / arXiv / Crossref / PubMed 用官方免 key API
(wikipedia zh 查空回退 en),SearXNG 实例列表来自 searx.space(本地缓存
一个月,超一周且非计费网络自动更新,超一个月强制更新)。并行查询后按 URL
合并去重。curl_cffi 指纹直抓(与 webgrab 同源),单引擎被拦或失败只跳过该
引擎,不影响其他。结尾提示用 `webgrab <url>` 抓正文。
"""

from __future__ import annotations

import argparse
import base64
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


def _fetch(url: str, timeout: float) -> str:
    """curl_cffi 指纹直抓,返回 HTML。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get(url, headers=EXTRA_HEADERS, allow_redirects=True)
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return r.text


def _fetch_json(url: str, params: dict, timeout: float, headers: dict | None = None) -> dict:
    """curl_cffi GET JSON,非 200 时抛出。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get(url, params=params, headers=headers or {})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
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


def parse_baidu(html: str) -> list[dict]:
    """解析百度结果页(result/c-container 块)。

    链接全是 baidu.com/link?url= 会话跳转,真 URL 由引擎函数逐条 302 解出。
    摘要容器 class 按模板乱变,取整块文本去掉标题前缀。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for b in soup.select("div.result, div.c-container"):
        a = b.select_one("h3 a[href]")
        if not a:
            continue
        title = _text(a)
        body = _text(b)
        snippet = body[len(title):].strip()[:250] if body.startswith(title) else body[:250]
        out.append({"url": a["href"], "title": title, "snippet": snippet})
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


def parse_searx(data: dict) -> list[dict]:
    """解析 SearXNG /search?format=json 响应(results[].url/title/content)。"""
    out = []
    for r in data.get("results", []):
        if str(r.get("url", "")).startswith("http"):
            out.append({"url": r["url"], "title": " ".join(r.get("title", "").split()),
                        "snippet": " ".join(str(r.get("content", "")).split())})
    return out


# searx.space 实例列表缓存:搜索成功率高 + https 的实例,按搜索耗时排序。
# 刷新策略:<7d 用缓存;>7d 且非计费网络(热点)自动刷;>30d 强制刷(计费也刷)。
SEARX_INSTANCES_URL = "https://searx.space/data/instances.json"
SEARX_CACHE = Path(
    os.environ.get("WEBSEARCH_SEARX_CACHE")
    or Path.home() / ".cache/lazygophers/scripts/searx-instances.json"
)
SEARX_MAX_ATTEMPTS = 12  # 单次搜索最多试几个实例(都是公益实例,省着用)


def _metered() -> bool:
    """计费网络 = 热点(iPhone/Android 共享,典型按流量计费)。"""
    try:
        from lib.ipinfo import is_hotspot_wifi
        return is_hotspot_wifi()
    except Exception:
        return False


def _searx_healthy(d: dict) -> list[str]:
    """instances.json → 健康 https 实例 URL 列表(按 searx.space 实测搜索耗时排序)。"""
    scored = []
    for url, info in d.get("instances", {}).items():
        if not url.startswith("https://"):
            continue
        if (info.get("http") or {}).get("status_code") != 200:
            continue
        search = (info.get("timing") or {}).get("search") or {}
        if search.get("success_percentage", 0) < 80:
            continue
        t = search.get("all")
        t = t.get("value") if isinstance(t, dict) else t
        scored.append((url, t if isinstance(t, (int, float)) else 9999))
    return [u for u, _t in sorted(scored, key=lambda x: x[1])]


def load_searx_instances(timeout: float = 15) -> list[str]:
    """读实例列表,按缓存策略决定是否重新拉 searx.space。"""
    import json
    import time

    cache = None
    if SEARX_CACHE.exists():
        try:
            cache = json.loads(SEARX_CACHE.read_text())
        except ValueError:
            cache = None
    age_days = (time.time() - cache["fetched_at"]) / 86400 if cache else None

    def refresh() -> list[str]:
        data = _fetch_json(SEARX_INSTANCES_URL, {}, timeout)
        urls = _searx_healthy(data)
        if not urls:
            raise SearchError("searx.space 无健康实例")
        SEARX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEARX_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "instances": urls}))
        tmp.replace(SEARX_CACHE)
        return urls

    if cache is None:
        return refresh()
    if age_days > 30:
        print(f"[websearch] searx.space 缓存 {age_days:.0f} 天,超过一个月,强制更新", file=sys.stderr)
    elif age_days > 7 and not _metered():
        print(f"[websearch] searx.space 缓存 {age_days:.0f} 天,超过一周且非计费网络,自动更新", file=sys.stderr)
    else:
        return cache["instances"]
    try:
        return refresh()
    except Exception as e:  # 刷新失败(网络),有旧缓存就用旧的
        print(f"[websearch] searx.space 更新失败({e}),用旧缓存", file=sys.stderr)
        return cache["instances"]


# 引擎定义:(名称, 取结果函数名, 签名 (query, timeout))。
# 存函数名而非引用,调用时经模块属性解析 —— 方便测试 mock.patch。
# 单引擎失败(被拦/网络)只跳过,不影响其余引擎;并行查询。
def _e_ddg(query, timeout, limit):
    return parse_ddg(_fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout))


def _e_ddg_lite(query, timeout, limit):
    return parse_ddg_lite(_fetch("https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), timeout))


def _e_bing(query, timeout, limit):
    return parse_bing(_fetch("https://www.bing.com/search?q=" + quote_plus(query), timeout))


def _e_google(query, timeout, limit):
    # 注意: Google 按出口 IP 风控,被标记的 IP 会拿到「请启用 JS」/ reCAPTCHA
    # 中间页(解析为 0 条,自动跳过该引擎);渲染也过不了 reCAPTCHA,不做回退
    return parse_google(_fetch("https://www.google.com/search?q=" + quote_plus(query), timeout))


def _e_yandex(query, timeout, limit):
    return parse_yandex(_fetch("https://yandex.com/search/?text=" + quote_plus(query), timeout))


def _e_github(query, timeout, limit):
    return parse_github(_fetch("https://github.com/search?type=repositories&q=" + quote_plus(query),
                               timeout))


def _e_wikipedia(query, timeout, limit):
    """Wikipedia 免 key 官方 API;zh 查空回退 en。"""
    from curl_cffi import requests

    params = {"action": "query", "list": "search", "format": "json", "srlimit": limit}
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


def _e_sogou(query, timeout, limit):
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


def _e_baidu(query, timeout, limit):
    """百度;结果链接是 baidu.com/link?url= 302 跳转,逐条解出真 URL(约 40ms/条)。"""
    from curl_cffi import requests

    items = parse_baidu(_fetch("https://www.baidu.com/s?wd=" + quote_plus(query), timeout))[:limit]
    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        for it in items:
            if "baidu.com/link?" in it["url"]:
                r = s.get(it["url"], allow_redirects=False)
                real = r.headers.get("Location", "")
                if real.startswith("http"):
                    it["url"] = real
    return [it for it in items if it["url"].startswith("http")]


def _e_360(query, timeout, limit):
    return parse_360(_fetch("https://www.so.com/s?q=" + quote_plus(query), timeout))


def _e_arxiv(query, timeout, limit):
    """arXiv 官方免 key API(Atom XML)。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get("https://export.arxiv.org/api/query",
                  params={"search_query": "all:" + query, "max_results": limit})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return parse_arxiv(r.text)


def _e_crossref(query, timeout, limit):
    """Crossref 官方免 key API(跨库论文 DOI 元数据)。"""
    from curl_cffi import requests

    with requests.Session(impersonate="chrome", timeout=timeout) as s:
        r = s.get("https://api.crossref.org/works",
                  params={"query": query, "rows": limit,
                          "select": "title,URL,abstract"})
        if r.status_code != 200:
            raise SearchError(f"HTTP {r.status_code}")
        return parse_crossref(r.json())


def _e_pubmed(query, timeout, limit):
    """PubMed 官方免 key API(esearch 拿 id → esummary 拿标题)。

    eutils 对 chrome TLS 指纹直接 reset,这里用朴素请求。
    """
    from curl_cffi import requests

    with requests.Session(timeout=timeout) as s:
        r = s.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                  params={"db": "pubmed", "term": query, "retmode": "json", "retmax": limit})
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


def _promote_searx(url: str, instances: list[str]) -> None:
    """把刚成功的实例挪到缓存列表最前(下次优先用),原地更新缓存文件。"""
    import json

    if not SEARX_CACHE.exists() or (instances and instances[0] == url):
        return
    try:
        cache = json.loads(SEARX_CACHE.read_text())
    except ValueError:
        return
    insts = [u for u in cache.get("instances", []) if u != url]
    cache["instances"] = [url] + insts
    SEARX_CACHE.write_text(json.dumps(cache))


def _e_searx(query, timeout, limit):
    """SearXNG 元搜索:实例列表来自 searx.space(本地缓存),逐个试到出结果。"""
    instances = load_searx_instances(timeout)
    errors = []
    for url in instances[:SEARX_MAX_ATTEMPTS]:
        try:
            data = _fetch_json(url.rstrip("/") + "/search",
                               {"q": query, "format": "json", "language": "auto"},
                               min(timeout, 6), {"Accept": "application/json"})
            items = parse_searx(data)
            if items:
                _promote_searx(url, instances)
                print(f"[websearch] searx 命中实例 {url}", file=sys.stderr)
                return items
        except Exception as e:  # 403/429/慢,换下一个实例
            errors.append(f"{url}: {e}")
    raise SearchError("; ".join(errors[-2:]) or "无可用实例")


ENGINES: list[tuple[str, str]] = [
    ("ddg", "_e_ddg"),
    ("ddg-lite", "_e_ddg_lite"),
    ("bing", "_e_bing"),
    ("google", "_e_google"),
    ("yandex", "_e_yandex"),
    ("sogou", "_e_sogou"),
    ("baidu", "_e_baidu"),
    ("360", "_e_360"),
    ("arxiv", "_e_arxiv"),
    ("crossref", "_e_crossref"),
    ("pubmed", "_e_pubmed"),
    ("searx", "_e_searx"),
    ("wikipedia", "_e_wikipedia"),
    ("github", "_e_github"),
]


# 默认只启用这些引擎;其余(ddg-lite/yandex/搜狗/360/crossref/pubmed)要
# --engine 指定或在配置文件 engines: 里列出才参与。
DEFAULT_ENGINES = ["ddg", "bing", "google", "searx", "wikipedia", "github", "arxiv"]


def load_config() -> dict:
    """读 ~/.config/lazygophers/scripts/websearch.yaml(可被 WEBSEARCH_CONFIG 覆盖)。"""
    import yaml

    try:
        data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _active_engines(engine: str | None) -> list[str]:
    """本次参与的引擎:--engine 指定 > 配置文件 engines: > DEFAULT_ENGINES。"""
    if engine:
        names = [engine]
    else:
        cfg = load_config().get("engines")
        names = [e.strip() for e in cfg if str(e).strip()] if isinstance(cfg, list) else DEFAULT_ENGINES
    known = {n for n, _ in ENGINES}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SearchError(f"未知引擎: {', '.join(unknown)}(可选: {', '.join(n for n, _ in ENGINES)})")
    return names


def search(query: str, limit: int = 10, engine: str | None = None,
           timeout: float = 15) -> list[dict]:
    """并行检索参与引擎(limit = 每引擎抓取条数),按 URL 合并去重,
    返回全部去重结果(首见顺序保留)。"""
    from concurrent.futures import ThreadPoolExecutor

    mod = sys.modules[__name__]
    names = _active_engines(engine)
    chain = [(n, getattr(mod, fn)) for n, fn in ENGINES if n in names]

    def _run(nf):
        name, fn = nf
        try:
            return name, fn(query, timeout, limit), ""
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
            # 去重键做归一化:百分号解码 + 去尾斜杠,同页不同写法算同一条
            key = unquote(it["url"]).rstrip("/")
            if key not in seen:
                seen.add(key)
                results.append(it)
    if not results:
        raise SearchError("所有引擎都没有结果: " + "; ".join(errors or ["解析到 0 条"]))
    return results


def list_engines() -> int:
    """打印全部引擎 + 默认启用状态 + searx 当前实例列表。"""
    active = set(_active_engines(None))
    for i, (name, _fn) in enumerate(ENGINES, 1):
        mark = "*" if name in active else " "
        print(f"{mark}{i}. {name}")
    print(f"\nsearx 当前实例(缓存 {SEARX_CACHE},前 {SEARX_MAX_ATTEMPTS} 个生效):")
    try:
        for u in load_searx_instances()[:SEARX_MAX_ATTEMPTS]:
            print(f"  {u}")
    except Exception as e:
        print(f"  (读取失败: {e})", file=sys.stderr)
    print("[websearch] * = 默认启用;--engine <名称> 单独指定,或 `websearch set engines <名称...>` 改默认集", file=sys.stderr)
    return 0


def set_engines(rest: list[str]) -> int:
    """`websearch set engines <名称...> [--reset]`:写配置文件的 engines: 列表。"""
    import yaml

    if rest[:1] != ["engines"]:
        print("用法: websearch set engines <名称...> | websearch set engines --reset", file=sys.stderr)
        return 2
    args = rest[1:]
    if not args:
        # 不带参数 = 打印当前生效的引擎集
        print("当前默认引擎:", " ".join(_active_engines(None)))
        print(f"配置文件: {CONFIG_PATH}", file=sys.stderr)
        return 0
    cfg = load_config()
    if "--reset" in args:
        cfg.pop("engines", None)  # 回到内置 DEFAULT_ENGINES
        note = "已删除 engines 配置,恢复内置默认集"
    else:
        unknown = [a for a in args if a not in {n for n, _ in ENGINES}]
        if unknown:
            print(f"未知引擎: {', '.join(unknown)}(可选: {', '.join(n for n, _ in ENGINES)})",
                  file=sys.stderr)
            return 2
        cfg["engines"] = args
        note = f"默认引擎集已写入 {CONFIG_PATH}: {' '.join(args)}"
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    tmp.replace(CONFIG_PATH)
    print(note)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="websearch",
        description="多引擎网页检索(全免 key,每引擎抓 limit 条后按 URL 合并去重),输出 标题 / URL / 摘要",
        epilog="子命令:\n"
               "  websearch engines                  列出全部引擎(* = 默认启用)\n"
               "  websearch set engines <名称...>    改默认引擎集(写配置文件)\n"
               "  websearch set engines --reset      恢复内置默认引擎集\n"
               "\n"
               "结果只有摘要,要看正文用: webgrab <url>",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("query", nargs="+", help="搜索词(多词直接跟在后面)")
    p.add_argument("-n", "--limit", type=int, default=10,
                   help="每个引擎抓几条(默认 10;合并去重后可能少于引擎总数)")
    p.add_argument("--engine", choices=[n for n, _ in ENGINES],
                   help="只用指定引擎(默认全部引擎)")
    p.add_argument("-f", "--format", choices=list(FORMATTERS), default="plain",
                   help="输出格式(默认 plain;tsv/csv 适合管道,table 用 Rich 表格)")
    p.add_argument("--json", action="store_true",
                   help="等价 --format json(管道给 jq 用)")
    p.add_argument("--timeout", type=float, default=15, help="单引擎超时秒数(默认 15)")
    return p


def _fmt_plain(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['title']}")
        print(f"   {r['url']}")
        if r["snippet"]:
            print(f"   {r['snippet']}")


def _fmt_json(results: list[dict]) -> None:
    import json

    print(json.dumps(results, ensure_ascii=False, indent=2))


def _fmt_tsv(results: list[dict]) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")

    print("index\turl\ttitle\tsnippet")
    for i, r in enumerate(results, 1):
        print(f"{i}\t{esc(r['url'])}\t{esc(r['title'])}\t{esc(r['snippet'])}")


def _fmt_csv(results: list[dict]) -> None:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["index", "url", "title", "snippet"])
    for i, r in enumerate(results, 1):
        w.writerow([i, r["url"], r["title"], r["snippet"]])
    print(buf.getvalue(), end="")


def _fmt_table(results: list[dict]) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    from lib.ui import Reporter

    table = Table(box=ROUNDED, title_justify="left")
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("标题", ratio=2, overflow="fold")
    table.add_column("URL", ratio=2, overflow="fold", style="cyan")
    table.add_column("摘要", ratio=3, overflow="fold")
    for i, r in enumerate(results, 1):
        table.add_row(str(i), r["title"], r["url"], r["snippet"])
    Reporter(stderr=False).console.print(table)  # 表格是结果,走 stdout


FORMATTERS = {"plain": _fmt_plain, "json": _fmt_json, "tsv": _fmt_tsv,
              "csv": _fmt_csv, "table": _fmt_table}


def main(argv: list[str] | None = None) -> int:
    rest = argv[1:] if argv is not None else None
    if not rest:
        # 裸跑默认打 AI 向 skills 说明(--skills 同款)
        from lib.skills_help import command_name, render_skills
        print(render_skills(command_name(argv[0]), __doc__))
        return 0
    # engines / set 子命令直接处理,不进 argparse
    if rest[0] == "engines":
        return list_engines()
    if rest[0] == "set":
        return set_engines(rest[1:])
    args = build_parser().parse_args(rest)
    try:
        results = search(" ".join(args.query), limit=args.limit,
                         engine=args.engine, timeout=args.timeout)
    except SearchError as e:
        print(f"[websearch] 检索失败: {e}", file=sys.stderr)
        return 1
    FORMATTERS["json" if args.json else args.format](results)
    print("[websearch] 看正文: webgrab <url>", file=sys.stderr)
    return 0
