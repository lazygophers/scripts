"""websearch 单元测试:解析器、去重、引擎合并、CLI 输出。"""

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from lib import websearch

DDG_HTML = """
<div class="result">
  <h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc">Example A</a></h2>
  <a class="result__snippet">Snippet A here</a>
</div>
<div class="result">
  <h2 class="result__title"><a class="result__a" href="https://example.com/b">Example B</a></h2>
</div>
<div class="result">
  <h2 class="result__title"><a class="result__a" href="/about">non-http dropped</a></h2>
</div>
"""

DDG_LITE_HTML = """
<table>
  <tr><td><a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Flite1">Lite One</a></td></tr>
  <tr><td><a rel="nofollow" href="">1. More Results</a></td></tr>
  <tr><td class="result-snippet">Lite snippet one</td></tr>
  <tr><td><a class="result-link" href="https://example.com/lite2">Lite Two</a></td></tr>
</table>
"""

BING_HTML = """
<li class="b_algo">
  <h2><a href="https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9iaW5nMQ==&ntb=1">Bing One</a></h2>
  <div class="b_caption"><p>Bing snippet one</p></div>
</li>
<li class="b_algo">
  <h2><a href="/internal">dropped</a></h2>
</li>
"""

GOOGLE_HTML = """
<div><a href="https://example.com/g1"><h3>Google One</h3></a></div>
<div><a href="https://www.google.com/search?q=x"><h3>internal link dropped</h3></a></div>
"""

YANDEX_HTML = """
<li class="serp-item">
  <a class="Link" href="https://example.com/y1"><h2>Yandex One</h2></a>
  <span class="OrganicTextContentSpan">Yandex snippet one</span>
</li>
<li class="serp-item">
  <a class="Link" href="/internal">dropped</a>
</li>
"""

GITHUB_HTML = """
<div data-testid="results-list">
  <div><div class="search-title"><a href="/owner/repo">owner/ repo</a></div>
       <span class="search-match">owner/ repo</span>
       <span class="search-match">Repo description here</span></div>
  <div><div class="search-title"><a href="https://github.com/o2/r2">o2/ r2</a></div></div>
  <div>no title dropped</div>
</div>
"""

WIKI_JSON = {
    "query": {
        "search": [
            {"title": "Python (lang)", "snippet": "<b>Python</b> is a language"},
            {"title": "No snippet"},
        ]
    }
}

SOGOU_HTML = """
<div class="vrwrap"><h3><a href="/link?url=abc">知乎结果</a></h3>
  <div class="text-layout">搜狗摘要文本</div></div>
<div class="rb"><h3><a href="https://mp.weixin.qq.com/s?x=1">微信结果</a></h3></div>
<div class="vrwrap"><span>无标题 dropped</span></div>
"""

SO360_HTML = """
<li class="res-list">
  <h3><a data-mdurl="https://example.com/real" href="https://www.so.com/link?m=x">真实链接结果</a></h3>
  <div class="res-rich">360 摘要文本</div>
</li>
<li class="res-list">
  <h3><a href="https://www.so.com/link?m=y">无 mdurl 跳转链 dropped</a></h3>
</li>
"""

ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <id>http://arxiv.org/abs/2401.00001v1</id>
  <title>Attention Paper  Title</title>
  <summary>  We study  attention.  </summary>
</entry>
<entry>
  <id>not-a-url dropped</id>
  <title>dropped</title>
</entry>
</feed>
"""

CROSSREF_JSON = {
    "message": {
        "items": [
            {"title": ["Paper One"], "URL": "https://doi.org/10.1/one",
             "abstract": "<jats:p>Paper abstract</jats:p>"},
            {"title": ["No URL dropped"], "URL": ""},
        ]
    }
}


class TestParsers(unittest.TestCase):
    def test_parse_ddg_unwraps_redirect_and_drops_non_http(self):
        items = websearch.parse_ddg(DDG_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertEqual(items[0]["title"], "Example A")
        self.assertEqual(items[0]["snippet"], "Snippet A here")
        self.assertEqual(items[1]["url"], "https://example.com/b")

    def test_parse_ddg_lite(self):
        items = websearch.parse_ddg_lite(DDG_LITE_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://example.com/lite1")
        self.assertEqual(items[0]["snippet"], "Lite snippet one")
        self.assertEqual(items[1]["url"], "https://example.com/lite2")

    def test_parse_bing_unwraps_ck_redirect(self):
        items = websearch.parse_bing(BING_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/bing1")
        self.assertEqual(items[0]["snippet"], "Bing snippet one")

    def test_unwrap_bing_invalid_base64_keeps_original(self):
        href = "https://www.bing.com/ck/a?u=a1###not-b64"
        self.assertEqual(websearch._unwrap_bing(href), href)

    def test_parse_google_h3_anchor_structure(self):
        items = websearch.parse_google(GOOGLE_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/g1")
        self.assertEqual(items[0]["title"], "Google One")

    def test_parse_yandex(self):
        items = websearch.parse_yandex(YANDEX_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/y1")
        self.assertEqual(items[0]["title"], "Yandex One")
        self.assertEqual(items[0]["snippet"], "Yandex snippet one")

    def test_parse_github(self):
        items = websearch.parse_github(GITHUB_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://github.com/owner/repo")
        self.assertEqual(items[0]["snippet"], "Repo description here")
        self.assertEqual(items[1]["url"], "https://github.com/o2/r2")

    def test_parse_wikipedia(self):
        items = websearch.parse_wikipedia(WIKI_JSON)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"],
                         "https://zh.wikipedia.org/wiki/Python_%28lang%29")
        self.assertEqual(items[0]["snippet"], "Python is a language")
        en = websearch.parse_wikipedia(WIKI_JSON, lang="en")
        self.assertEqual(en[0]["url"], "https://en.wikipedia.org/wiki/Python_%28lang%29")

    def test_parse_sogou_keeps_link_for_engine_to_resolve(self):
        items = websearch.parse_sogou(SOGOU_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "/link?url=abc")  # 解真 URL 是 _e_sogou 的活
        self.assertEqual(items[0]["snippet"], "搜狗摘要文本")
        self.assertEqual(items[1]["url"], "https://mp.weixin.qq.com/s?x=1")

    def test_parse_360_uses_mdurl(self):
        items = websearch.parse_360(SO360_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/real")
        self.assertEqual(items[0]["snippet"], "360 摘要文本")

    def test_parse_arxiv(self):
        items = websearch.parse_arxiv(ARXIV_XML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "http://arxiv.org/abs/2401.00001v1")
        self.assertEqual(items[0]["title"], "Attention Paper Title")
        self.assertEqual(items[0]["snippet"], "We study attention.")

    def test_parse_crossref(self):
        items = websearch.parse_crossref(CROSSREF_JSON)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://doi.org/10.1/one")
        self.assertEqual(items[0]["title"], "Paper One")
        self.assertEqual(items[0]["snippet"], "Paper abstract")


class TestSearch(unittest.TestCase):
    """全部 _e_* 打桩,专测合并逻辑。"""

    def _patch_all(self, **overrides):
        """overrides: 引擎名 → Mock(return_value/side_effect 自定);未给的引擎打 [] 桩。"""
        patches = []
        for name, fn in websearch.ENGINES:
            m = overrides.get(name.replace("-", "_"))
            if m is None:
                m = mock.Mock(return_value=[])
            patches.append((fn, m))
        return mock.patch.multiple(websearch, **dict(patches))

    def test_all_engines_queried_and_merged_by_url(self):
        bing_dup = BING_HTML.replace(
            "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9iaW5nMQ==",
            "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9h",
        )
        with self._patch_all(
            ddg=mock.Mock(return_value=websearch.parse_ddg(DDG_HTML)),
            ddg_lite=mock.Mock(return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)),
            bing=mock.Mock(return_value=websearch.parse_bing(bing_dup)),
            yandex=mock.Mock(return_value=websearch.parse_yandex(YANDEX_HTML)),
            wikipedia=mock.Mock(return_value=websearch.parse_wikipedia(WIKI_JSON)),
            github=mock.Mock(return_value=websearch.parse_github(GITHUB_HTML)),
        ):
            items = websearch.search("q", limit=10)
        # bing 的 example.com/a 与 ddg 首条同 URL,去重后只留 ddg 版本
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertEqual(items[0]["snippet"], "Snippet A here")
        urls = [i["url"] for i in items]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 9)  # ddg2 + lite2 + bing去重0 + yandex1 + wiki2 + github2

    def test_limit_truncates_after_merge(self):
        with self._patch_all(
            ddg=mock.Mock(return_value=websearch.parse_ddg(DDG_HTML)),
            ddg_lite=mock.Mock(return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)),
        ):
            items = websearch.search("q", limit=3)
        self.assertEqual(len(items), 3)

    def test_engine_error_skipped_others_continue(self):
        with self._patch_all(
            ddg=mock.Mock(side_effect=OSError("down")),
            ddg_lite=mock.Mock(return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)),
        ):
            items = websearch.search("q")
        self.assertIn("https://example.com/lite1", [i["url"] for i in items])

    def test_all_engines_fail_raises(self):
        with self._patch_all(**{
            name: mock.Mock(side_effect=OSError("down")) for name, _fn in websearch.ENGINES
        }):
            with self.assertRaises(websearch.SearchError):
                websearch.search("q")

    def test_unknown_engine_raises(self):
        with self.assertRaises(websearch.SearchError):
            websearch.search("q", engine="google-x")


class TestCli(unittest.TestCase):
    def test_main_plain_output(self):
        fake = [{"url": "https://example.com/x", "title": "T", "snippet": "S"}]
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(websearch, "search", return_value=fake):
            with redirect_stdout(buf), redirect_stderr(err):
                rc = websearch.main(["websearch", "hello", "world"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("1. T", out)
        self.assertIn("https://example.com/x", out)
        self.assertIn("S", out)
        self.assertIn("webgrab <url>", err.getvalue())

    def test_main_json_output(self):
        fake = [{"url": "https://example.com/x", "title": "T", "snippet": "S"}]
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(websearch, "search", return_value=fake):
            with redirect_stdout(buf), redirect_stderr(err):
                rc = websearch.main(["websearch", "--json", "q"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), fake)

    def test_main_no_args_prints_skills(self):
        buf = io.StringIO()
        with mock.patch.object(websearch, "search") as s:
            with redirect_stdout(buf):
                rc = websearch.main(["websearch"])
        self.assertEqual(rc, 0)
        self.assertIn("# websearch skills", buf.getvalue())
        s.assert_not_called()

    def test_main_engines_lists_all(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = websearch.main(["websearch", "engines"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for name, _fn in websearch.ENGINES:
            self.assertIn(name, out)

    def test_main_failure_exit_1(self):
        err = io.StringIO()
        with mock.patch.object(websearch, "search",
                               side_effect=websearch.SearchError("nope")):
            with redirect_stderr(err):
                rc = websearch.main(["websearch", "q"])
        self.assertEqual(rc, 1)
        self.assertIn("检索失败", err.getvalue())


if __name__ == "__main__":
    unittest.main()
