"""websearch 单元测试:解析器、去重、引擎合并、key 引擎、CLI 输出。"""

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
  <h2><a href="https://example.com/bing1">Bing One</a></h2>
  <div class="b_caption"><p>Bing snippet one</p></div>
</li>
<li class="b_algo">
  <h2><a href="/internal">dropped</a></h2>
</li>
"""

GOOGLE_JSON = {
    "items": [
        {"link": "https://example.com/a", "title": "Example A", "snippet": "Google snippet"},
        {"link": "https://example.com/g2", "title": "G Two", "snippet": ""},
        {"title": "no link dropped"},
    ]
}

BRAVE_JSON = {
    "web": {
        "results": [
            {"url": "https://example.com/br1", "title": "Brave One", "description": "Brave snippet"},
            {"url": "", "title": "no url dropped"},
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

    def test_parse_bing(self):
        items = websearch.parse_bing(BING_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/bing1")
        self.assertEqual(items[0]["snippet"], "Bing snippet one")

    def test_parse_google(self):
        items = websearch.parse_google(GOOGLE_JSON)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertEqual(items[0]["snippet"], "Google snippet")

    def test_parse_brave(self):
        items = websearch.parse_brave(BRAVE_JSON)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/br1")
        self.assertEqual(items[0]["snippet"], "Brave snippet")


class TestSearch(unittest.TestCase):
    def _no_keys(self):
        return mock.patch.object(websearch, "load_keys", return_value={})

    def test_keyless_only_when_no_config(self):
        with self._no_keys():
            # google/brave 没配置被跳过: 三个免 key 引擎取数函数被各调一次
            with mock.patch.object(websearch, "_e_ddg",
                                   return_value=websearch.parse_ddg(DDG_HTML)) as ddg, \
                 mock.patch.object(websearch, "_e_ddg_lite", return_value=[]) as lite, \
                 mock.patch.object(websearch, "_e_bing", return_value=[]) as bing:
                websearch.search("q")
        ddg.assert_called_once()
        lite.assert_called_once()
        bing.assert_called_once()

    def test_all_engines_queried_and_merged_by_url(self):
        bing = BING_HTML.replace("https://example.com/bing1", "https://example.com/a")
        google_items = websearch.parse_google(GOOGLE_JSON)
        keys = {"google": {"api_key": "k", "cx": "c"}, "brave": {"api_key": "b"}}
        with mock.patch.object(websearch, "load_keys", return_value=keys):
            with mock.patch.object(websearch, "_e_google", return_value=google_items) as g, \
                 mock.patch.object(websearch, "_e_brave",
                                   mock.Mock(return_value=[{"url": "https://example.com/br1",
                                                            "title": "Brave One",
                                                            "snippet": "s"}])) as b, \
                 mock.patch.object(websearch, "_e_ddg", return_value=websearch.parse_ddg(DDG_HTML)), \
                 mock.patch.object(websearch, "_e_ddg_lite",
                                   return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)), \
                 mock.patch.object(websearch, "_e_bing",
                                   mock.Mock(return_value=websearch.parse_bing(bing))):
                items = websearch.search("q", limit=10)
        g.assert_called_once()
        b.assert_called_once()
        # google 排最前,其 Example A 先占位;bing 的同 URL 版本被去重
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertEqual(items[0]["snippet"], "Google snippet")
        urls = [i["url"] for i in items]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("https://example.com/br1", urls)

    def test_limit_truncates_after_merge(self):
        with self._no_keys():
            with mock.patch.object(websearch, "_e_ddg", return_value=websearch.parse_ddg(DDG_HTML)), \
                 mock.patch.object(websearch, "_e_ddg_lite",
                                   return_value=websearch.parse_ddg_lite(DDG_LITE_HTML)), \
                 mock.patch.object(websearch, "_e_bing", return_value=[]):
                items = websearch.search("q", limit=3)
        self.assertEqual(len(items), 3)

    def test_engine_not_configured_raises_when_forced(self):
        with self._no_keys():
            with self.assertRaises(websearch.SearchError) as cm:
                websearch.search("q", engine="google")
        self.assertIn("未配置", str(cm.exception))

    def test_engine_not_configured_skipped_when_default(self):
        with self._no_keys():
            with mock.patch.object(websearch, "_e_ddg", return_value=websearch.parse_ddg(DDG_HTML)), \
                 mock.patch.object(websearch, "_e_ddg_lite", return_value=[]), \
                 mock.patch.object(websearch, "_e_bing", return_value=[]):
                items = websearch.search("q")
        self.assertEqual(len(items), 2)  # 正常返回,key 引擎静默跳过

    def test_all_engines_fail_raises(self):
        with self._no_keys():
            with mock.patch.object(websearch, "_e_ddg", side_effect=OSError("down")), \
                 mock.patch.object(websearch, "_e_ddg_lite", side_effect=OSError("down")), \
                 mock.patch.object(websearch, "_e_bing", side_effect=OSError("down")):
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

    def test_main_engines_lists_all_and_key_status(self):
        buf = io.StringIO()
        with mock.patch.object(websearch, "load_keys",
                               return_value={"google": {"api_key": "abcd1234", "cx": "cx1"}}):
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = websearch.main(["websearch", "engines"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for name, _, _ in websearch.ENGINES:
            self.assertIn(name, out)
        self.assertIn("abcd***", out)
        self.assertIn("已配置", out)
        self.assertIn("未配置(跳过)", out)

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
