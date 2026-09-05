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
<div><a href="https://example.com/g1"><div>Google snippet one</div></a></div>
<div><a href="https://www.google.com/search?q=x"><h3>internal link dropped</h3></a></div>
<div><span>no link h3 kept as title source only when parented by a</span></div>
"""


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


class TestSearch(unittest.TestCase):
    def test_all_engines_queried_and_merged_by_url(self):
        # bing 首条编码为 https://example.com/a,与 ddg 首条同 URL 验证跨引擎去重
        bing_dup = BING_HTML.replace(
            "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9iaW5nMQ==",
            "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9h",
        )
        with mock.patch.object(websearch, "_e_ddg",
                               return_value=websearch.parse_ddg(DDG_HTML)) as ddg, \
             mock.patch.object(websearch, "_e_ddg_lite",
                               return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)) as lite, \
             mock.patch.object(websearch, "_e_bing",
                               return_value=websearch.parse_bing(bing_dup)) as bing, \
             mock.patch.object(websearch, "_e_google", return_value=[]) as goog:
            items = websearch.search("q", limit=10)
        ddg.assert_called_once()
        lite.assert_called_once()
        bing.assert_called_once()
        goog.assert_called_once()
        # bing 的 example.com/a 与 ddg 首条同 URL,去重后只留 ddg 版本
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertEqual(items[0]["snippet"], "Snippet A here")
        urls = [i["url"] for i in items]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 4)

    def test_limit_truncates_after_merge(self):
        with mock.patch.object(websearch, "_e_ddg",
                               return_value=websearch.parse_ddg(DDG_HTML)), \
             mock.patch.object(websearch, "_e_ddg_lite",
                               return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)), \
             mock.patch.object(websearch, "_e_bing", return_value=[]), \
             mock.patch.object(websearch, "_e_google", return_value=[]):
            items = websearch.search("q", limit=3)
        self.assertEqual(len(items), 3)

    def test_engine_error_skipped_others_continue(self):
        with mock.patch.object(websearch, "_e_ddg", side_effect=OSError("down")), \
             mock.patch.object(websearch, "_e_ddg_lite",
                               return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)), \
             mock.patch.object(websearch, "_e_bing", return_value=[]), \
             mock.patch.object(websearch, "_e_google", return_value=[]):
            items = websearch.search("q")
        self.assertEqual([i["url"] for i in items],
                         ["https://example.com/lite1", "https://example.com/lite2"])

    def test_all_engines_fail_raises(self):
        with mock.patch.object(websearch, "_e_ddg", side_effect=OSError("down")), \
             mock.patch.object(websearch, "_e_ddg_lite", side_effect=OSError("down")), \
             mock.patch.object(websearch, "_e_bing", side_effect=OSError("down")), \
             mock.patch.object(websearch, "_e_google", side_effect=OSError("down")):
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
