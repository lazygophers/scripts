"""websearch 单元测试:解析器、去重、引擎合并、CLI 输出。"""

import io
import pathlib
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

BAIDU_HTML = """
<div class="result"><h3><a href="http://www.baidu.com/link?url=x1">百度结果一</a></h3>
  <div>摘要文本</div></div>
<div class="c-container"><h3><a href="https://example.com/direct">直链结果</a></h3></div>
<div class="result"><span>无标题 dropped</span></div>
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

SEARX_JSON = {
    "results": [
        {"url": "https://example.com/s1", "title": " Searx  One ", "content": "  content  one "},
        {"url": "javascript:alert(1) dropped", "title": "x", "content": ""},
    ]
}

SEARX_INSTANCES_JSON = {
    "instances": {
        "https://fast.example/": {"http": {"status_code": 200},
                                  "timing": {"search": {"success_percentage": 100, "all": 0.5}}},
        "https://slow.example/": {"http": {"status_code": 200},
                                  "timing": {"search": {"success_percentage": 90, "all": 2.0}}},
        "http://insecure.example/": {"http": {"status_code": 200},
                                     "timing": {"search": {"success_percentage": 100}}},
        "https://down.example/": {"http": {"status_code": 502},
                                  "timing": {"search": {"success_percentage": 100}}},
        "https://flaky.example/": {"http": {"status_code": 200},
                                   "timing": {"search": {"success_percentage": 60}}},
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

    def test_parse_baidu_keeps_link_for_engine_to_resolve(self):
        items = websearch.parse_baidu(BAIDU_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "http://www.baidu.com/link?url=x1")  # 解真 URL 是 _e_baidu 的活
        self.assertEqual(items[0]["snippet"], "摘要文本")
        self.assertEqual(items[1]["url"], "https://example.com/direct")

    def test_e_baidu_resolves_link_redirects(self):
        resp = mock.Mock(status_code=302)
        resp.headers = {"Location": "https://example.com/real"}
        sess = mock.MagicMock()
        sess.__enter__.return_value.get.return_value = resp
        with mock.patch.object(websearch, "_fetch", return_value=BAIDU_HTML), \
             mock.patch("curl_cffi.requests.Session", return_value=sess):
            items = websearch._e_baidu("q", 5, 10)
        urls = [i["url"] for i in items]
        self.assertIn("https://example.com/real", urls)
        self.assertIn("https://example.com/direct", urls)
        self.assertNotIn("http://www.baidu.com/link?url=x1", urls)

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

    def test_parse_searx(self):
        items = websearch.parse_searx(SEARX_JSON)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/s1")
        self.assertEqual(items[0]["title"], "Searx One")
        self.assertEqual(items[0]["snippet"], "content one")


class TestSearxInstances(unittest.TestCase):
    """searx.space 实例缓存策略:tmp 目录当缓存,不打真网。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.cache = pathlib.Path(self._tmp.name) / "searx.json"
        patcher = mock.patch.object(websearch, "SEARX_CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_cache(self, age_days):
        import json
        import time

        self.cache.write_text(json.dumps(
            {"fetched_at": time.time() - age_days * 86400,
             "instances": ["https://cached.example/"]}))

    def _mock_source(self):
        return mock.patch.object(websearch, "_fetch_json",
                                 return_value=SEARX_INSTANCES_JSON)

    def test_fresh_cache_used_no_refresh(self):
        self._write_cache(3)
        with self._mock_source() as src:
            got = websearch.load_searx_instances()
        src.assert_not_called()
        self.assertEqual(got, ["https://cached.example/"])

    def test_over_week_non_metered_refreshes(self):
        self._write_cache(8)
        with mock.patch.object(websearch, "_metered", return_value=False), self._mock_source() as src:
            got = websearch.load_searx_instances()
        src.assert_called_once()
        self.assertEqual(got, ["https://fast.example/", "https://slow.example/"])

    def test_over_week_metered_keeps_cache(self):
        self._write_cache(8)
        with mock.patch.object(websearch, "_metered", return_value=True), self._mock_source() as src:
            got = websearch.load_searx_instances()
        src.assert_not_called()
        self.assertEqual(got, ["https://cached.example/"])

    def test_over_month_force_refresh_even_metered(self):
        self._write_cache(31)
        with mock.patch.object(websearch, "_metered", return_value=True), self._mock_source() as src:
            got = websearch.load_searx_instances()
        src.assert_called_once()
        self.assertEqual(got, ["https://fast.example/", "https://slow.example/"])

    def test_refresh_failure_falls_back_to_stale_cache(self):
        self._write_cache(40)
        with mock.patch.object(websearch, "_fetch_json", side_effect=OSError("net down")):
            got = websearch.load_searx_instances()
        self.assertEqual(got, ["https://cached.example/"])

    def test_promote_moves_winner_to_front(self):
        import json
        import time

        self.cache.write_text(json.dumps(
            {"fetched_at": time.time(),
             "instances": ["https://a/", "https://b/", "https://c/"]}))
        websearch._promote_searx("https://c/", ["https://a/", "https://b/", "https://c/"])
        self.assertEqual(json.loads(self.cache.read_text())["instances"],
                         ["https://c/", "https://a/", "https://b/"])


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
        with mock.patch.object(websearch, "_active_engines",
                               return_value=[n for n, _ in websearch.ENGINES]), \
             self._patch_all(
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

    def test_limit_is_per_engine_not_output_cap(self):
        # limit 传给每个引擎;合并结果不再截断
        seen = {}

        def fake(name, n):
            def fn(query, timeout, limit):
                seen[name] = limit
                return [{"url": f"https://example.com/{name}{i}"} for i in range(n)]
            return fn

        with mock.patch.multiple(
            websearch,
            _e_ddg=mock.MagicMock(side_effect=fake("ddg", 10)),
            _e_bing=mock.MagicMock(side_effect=fake("bing", 8)),
            _e_wikipedia=mock.MagicMock(return_value=[]),
            _e_google=mock.MagicMock(return_value=[]),
            _e_searx=mock.MagicMock(return_value=[]),
            _e_github=mock.MagicMock(return_value=[]),
            _e_arxiv=mock.MagicMock(return_value=[]),
        ):
            items = websearch.search("q", limit=8)
        self.assertEqual(seen, {"ddg": 8, "bing": 8})  # 每引擎都拿到 limit
        self.assertEqual(len(items), 18)  # 合并后不截断

    def test_default_engines_exclude_slow_set(self):
        names = set(websearch.DEFAULT_ENGINES)
        self.assertIn("ddg", names)
        for excluded in ("ddg-lite", "yandex", "sogou", "360", "crossref", "pubmed", "baidu"):
            self.assertNotIn(excluded, names)

    def test_config_engines_overrides_default(self):
        cfg = mock.patch.object(websearch, "load_config",
                                return_value={"engines": ["yandex", "360"]})
        with cfg:
            self.assertEqual(websearch._active_engines(None), ["yandex", "360"])

    def test_engine_flag_overrides_config(self):
        cfg = mock.patch.object(websearch, "load_config",
                                return_value={"engines": ["yandex"]})
        with cfg:
            self.assertEqual(websearch._active_engines("bing"), ["bing"])

    def test_active_engines_unknown_raises(self):
        with mock.patch.object(websearch, "load_config",
                               return_value={"engines": ["bogus"]}):
            with self.assertRaises(websearch.SearchError):
                websearch._active_engines(None)

    def test_engine_error_skipped_others_continue(self):
        with mock.patch.object(websearch, "_active_engines",
                               return_value=[n for n, _ in websearch.ENGINES]), \
             self._patch_all(
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

    def test_dedupe_normalizes_percent_encoding(self):
        a = {"url": "https://zh.wikipedia.org/wiki/%E4%B8%AD%E5%9B%BD", "title": "A", "snippet": ""}
        b = {"url": "https://zh.wikipedia.org/wiki/中国", "title": "B", "snippet": ""}
        with self._patch_all(
            ddg=mock.Mock(return_value=[a]),
            bing=mock.Mock(return_value=[b]),
        ):
            items = websearch.search("q")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A")


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

    def test_main_engines_lists_all_and_searx_instances(self):
        buf = io.StringIO()
        with mock.patch.object(websearch, "_active_engines", return_value=["ddg"]):
            with mock.patch.object(websearch, "load_searx_instances",
                                   return_value=["https://s1.example/", "https://s2.example/"]):
                with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                    rc = websearch.main(["websearch", "engines"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("*1. ddg", out)
        self.assertIn(" 3. bing", out)  # 非默认集不打 *
        self.assertIn("https://s1.example/", out)

    def test_set_engines_writes_config(self):
        import yaml

        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(websearch, "CONFIG_PATH",
                               pathlib.Path(self.id() + ".yaml")) as cfg_path:
            self.addCleanup(lambda: cfg_path.unlink(missing_ok=True))
            with redirect_stdout(buf), redirect_stderr(err):
                rc = websearch.main(["websearch", "set", "engines", "ddg", "yandex"])
            self.assertEqual(rc, 0)
            self.assertEqual(yaml.safe_load(cfg_path.read_text())["engines"], ["ddg", "yandex"])
            # 重置后回默认集
            with redirect_stdout(buf), redirect_stderr(err):
                rc = websearch.main(["websearch", "set", "engines", "--reset"])
            self.assertEqual(rc, 0)
            self.assertEqual(websearch.load_config(), {})

    def test_set_engines_unknown_rejected(self):
        with mock.patch.object(websearch, "CONFIG_PATH",
                               pathlib.Path(self.id() + ".yaml")) as cfg_path:
            self.addCleanup(lambda: cfg_path.unlink(missing_ok=True))
            err = io.StringIO()
            with redirect_stderr(err):
                rc = websearch.main(["websearch", "set", "engines", "bogus"])
        self.assertEqual(rc, 2)
        self.assertIn("未知引擎", err.getvalue())
        self.assertFalse(cfg_path.exists())  # 拒绝时不落盘

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
