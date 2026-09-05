"""websearch 单元测试:解析器、去重、引擎链回退、CLI 输出。"""

import io
import json
import sys
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
  <h2><a href="https://example.com/bing1">Bing One</a></h2>
  <div class="b_caption"><p>Bing snippet one</p></div>
</li>
<li class="b_algo">
  <h2><a href="/internal">dropped</a></h2>
</li>
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

    def test_parse_bing(self):
        items = websearch.parse_bing(BING_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/bing1")
        self.assertEqual(items[0]["snippet"], "Bing snippet one")


class TestSearch(unittest.TestCase):
    def test_chain_falls_through_on_error(self):
        with mock.patch.object(websearch, "_fetch", side_effect=OSError("boom")) as fetch:
            fetch.side_effect = [
                OSError("ddg down"),
                DDG_LITE_HTML,
            ]
            items = websearch.search("q", limit=5)
        self.assertEqual([i["url"] for i in items],
                         ["https://example.com/lite1", "https://example.com/lite2"])

    def test_dedupe_across_engines(self):
        with mock.patch.object(websearch, "_fetch") as fetch:
            fetch.side_effect = [DDG_HTML, DDG_HTML]  # 同一批结果出现两次
            items = websearch.search("q", limit=10)
        urls = [i["url"] for i in items]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 2)

    def test_all_engines_queried_and_merged_by_url(self):
        bing = """
        <li class="b_algo"><h2><a href="https://example.com/a">Example A(bing)</a></h2>
        <div class="b_caption"><p>Bing snippet</p></div></li>
        <li class="b_algo"><h2><a href="https://example.com/c">Example C</a></h2></li>
        """
        with mock.patch.object(websearch, "_fetch") as fetch:
            fetch.side_effect = [DDG_HTML, DDG_LITE_HTML, bing]
            items = websearch.search("q", limit=10)
        # 三个引擎都查了;首见顺序保留,重复 URL 只留第一条(ddg 的版本)
        self.assertEqual(
            [i["url"] for i in items],
            ["https://example.com/a", "https://example.com/b",
             "https://example.com/lite1", "https://example.com/lite2",
             "https://example.com/c"],
        )
        first = next(i for i in items if i["url"] == "https://example.com/a")
        self.assertEqual(first["title"], "Example A")
        self.assertEqual(fetch.call_count, 3)

    def test_limit_truncates_after_merge(self):
        with mock.patch.object(websearch, "_fetch") as fetch:
            fetch.side_effect = [DDG_HTML, DDG_LITE_HTML, ""]
            items = websearch.search("q", limit=3)
        self.assertEqual(len(items), 3)
        self.assertEqual(fetch.call_count, 3)  # limit 不提前截断引擎查询

    def test_all_engines_fail_raises(self):
        with mock.patch.object(websearch, "_fetch", side_effect=OSError("down")):
            with self.assertRaises(websearch.SearchError):
                websearch.search("q")

    def test_unknown_engine_raises(self):
        with self.assertRaises(websearch.SearchError):
            websearch.search("q", engine="google")


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

    def test_main_engines_lists_all(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = websearch.main(["websearch", "engines"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for name, _, _ in websearch.ENGINES:
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
