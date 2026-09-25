"""websearch 补充测试:抓取层、引擎函数、searx 实例边界、CLI 子命令。

tests/test_websearch.py 已覆盖解析器、合并去重和主要输出格式,这里只补它没跑到的
分支:_fetch / _fetch_json 的 HTTP 错误、各引擎的翻页 URL 与官方 API 分支、
searx 缓存损坏与实例轮换、list_engines / set_engines 的错误路径、AI 环境下
plain 降级 TSV。

全程不打真网:curl_cffi 的 Session 换成本文件里的假货,缓存和配置文件落在
tempfile.TemporaryDirectory()。
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from lib import websearch


class FakeResp:
    """假响应:只给 websearch 真正读的那几个属性。"""

    def __init__(self, status_code: int = 200, text: str = "",
                 payload=None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeSession:
    """假 curl_cffi Session:按队列顺序吐预设响应,并记录每次 get 的参数。"""

    def __init__(self, responses: list, calls: list) -> None:
        self._responses = responses
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self._responses:
            raise AssertionError(f"没有为 {url} 预备响应")
        return self._responses.pop(0)


def patch_session(*responses):
    """把 curl_cffi.requests.Session 换成 FakeSession,返回 (patcher, calls)。"""
    calls: list = []
    patcher = mock.patch("curl_cffi.requests.Session",
                         side_effect=lambda *a, **kw: FakeSession(list(responses), calls))
    return patcher, calls


class TestFetch(unittest.TestCase):
    """_fetch / _fetch_json:HTTP 层,非 200 一律转 SearchError。"""

    def test_fetch_returns_html_and_sends_extra_headers(self) -> None:
        patcher, calls = patch_session(FakeResp(text="<html>ok</html>"))
        with patcher:
            self.assertEqual(websearch._fetch("https://e.example/q", 5), "<html>ok</html>")
        self.assertEqual(calls[0][1]["headers"], websearch.EXTRA_HEADERS)

    def test_fetch_non_200_raises_search_error(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=429))
        with patcher, self.assertRaises(websearch.SearchError) as cm:
            websearch._fetch("https://e.example/q", 5)
        self.assertIn("429", str(cm.exception))

    def test_fetch_json_returns_payload(self) -> None:
        patcher, calls = patch_session(FakeResp(payload={"k": 1}))
        with patcher:
            got = websearch._fetch_json("https://e.example/api", {"q": "x"}, 5)
        self.assertEqual(got, {"k": 1})
        self.assertEqual(calls[0][1]["params"], {"q": "x"})

    def test_fetch_json_non_200_raises_search_error(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=503))
        with patcher, self.assertRaises(websearch.SearchError) as cm:
            websearch._fetch_json("https://e.example/api", {}, 5)
        self.assertIn("503", str(cm.exception))


class TestUnwrapBingBroken(unittest.TestCase):
    def test_undecodable_base64_keeps_original_href(self) -> None:
        # "a1A" 补齐后是 "A===",base64 解不了(1 个字符不可能是合法分组)
        href = "https://www.bing.com/ck/a?u=a1A"
        self.assertEqual(websearch._unwrap_bing(href), href)


class TestParserSkips(unittest.TestCase):
    """解析器的跳过分支:结构缺了就丢掉这一条,不抛异常。"""

    def test_ddg_result_without_anchor_is_skipped(self) -> None:
        self.assertEqual(websearch.parse_ddg('<div class="result"><h2>无链接</h2></div>'), [])

    def test_ddg_lite_non_http_link_is_skipped(self) -> None:
        self.assertEqual(
            websearch.parse_ddg_lite('<a class="result-link" href="/relative">x</a>'), [])

    def test_bing_item_without_h2_anchor_is_skipped(self) -> None:
        self.assertEqual(websearch.parse_bing('<li class="b_algo"><p>无标题链接</p></li>'), [])

    def test_google_h3_without_anchor_parent_is_skipped(self) -> None:
        self.assertEqual(websearch.parse_google("<div><h3>裸标题</h3></div>"), [])

    def test_google_takes_sibling_div_as_snippet(self) -> None:
        # 摘要取的是「锚点所在 div 的直接子 div」,标题那块跳过
        html = ('<div><a href="https://e.example/1"><h3>标题</h3></a>'
                '<div>这是摘要</div></div>')
        items = websearch.parse_google(html)
        self.assertEqual(items[0]["snippet"], "这是摘要")

    def test_360_item_without_h3_anchor_is_skipped(self) -> None:
        self.assertEqual(websearch.parse_360('<li class="res-list"><p>无标题</p></li>'), [])

    def test_360_non_http_url_is_skipped(self) -> None:
        html = '<li class="res-list"><h3><a href="javascript:;">x</a></h3></li>'
        self.assertEqual(websearch.parse_360(html), [])


class TestMetered(unittest.TestCase):
    """_metered:热点算计费网络;探测本身出错一律当作不计费。"""

    def test_hotspot_is_metered(self) -> None:
        with mock.patch("lib.ipinfo.is_hotspot_wifi", return_value=True):
            self.assertTrue(websearch._metered())

    def test_probe_failure_is_not_metered(self) -> None:
        with mock.patch("lib.ipinfo.is_hotspot_wifi", side_effect=OSError("没网卡")):
            self.assertFalse(websearch._metered())


class SearxCacheCase(unittest.TestCase):
    """公共脚手架:searx 缓存文件落到临时目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name) / "searx.json"
        p = mock.patch.object(websearch, "SEARX_CACHE", self.cache)
        p.start()
        self.addCleanup(p.stop)


class TestLoadSearxInstancesEdges(SearxCacheCase):
    def test_corrupt_cache_falls_back_to_refresh(self) -> None:
        self.cache.write_text("{不是 json", encoding="utf-8")
        payload = {"instances": {"https://ok.example/": {
            "http": {"status_code": 200},
            "timing": {"search": {"success_percentage": 99, "all": {"value": 1}}}}}}
        with mock.patch.object(websearch, "_fetch_json", return_value=payload):
            got = websearch.load_searx_instances()
        self.assertEqual(got, ["https://ok.example/"])

    def test_no_cache_refreshes_and_writes_file(self) -> None:
        payload = {"instances": {"https://ok.example/": {
            "http": {"status_code": 200},
            "timing": {"search": {"success_percentage": 99, "all": 2}}}}}
        with mock.patch.object(websearch, "_fetch_json", return_value=payload):
            websearch.load_searx_instances()
        self.assertEqual(json.loads(self.cache.read_text())["instances"], ["https://ok.example/"])

    def test_refresh_without_healthy_instance_raises(self) -> None:
        # 全部实例 success_percentage 不达标 → 一个都不留 → 报错而不是写空缓存
        payload = {"instances": {"https://bad.example/": {
            "http": {"status_code": 200},
            "timing": {"search": {"success_percentage": 10}}}}}
        with mock.patch.object(websearch, "_fetch_json", return_value=payload), \
             self.assertRaises(websearch.SearchError) as cm:
            websearch.load_searx_instances()
        self.assertIn("无健康实例", str(cm.exception))
        self.assertFalse(self.cache.exists())


class TestPromoteSearxEdges(SearxCacheCase):
    def test_no_write_when_winner_already_first(self) -> None:
        self.cache.write_text(json.dumps({"fetched_at": time.time(),
                                          "instances": ["https://a/", "https://b/"]}))
        before = self.cache.read_text()
        websearch._promote_searx("https://a/", ["https://a/", "https://b/"])
        self.assertEqual(self.cache.read_text(), before)

    def test_corrupt_cache_is_left_alone(self) -> None:
        self.cache.write_text("坏掉的缓存", encoding="utf-8")
        websearch._promote_searx("https://c/", ["https://a/"])
        self.assertEqual(self.cache.read_text(), "坏掉的缓存")


class TestEngineUrls(unittest.TestCase):
    """翻页参数:每个引擎的 offset 写法都不一样,逐个钉住。"""

    def _url(self, fn, *, page: int, limit: int = 10) -> str:
        with mock.patch.object(websearch, "_fetch", return_value="") as fetch:
            fn("查询 词", 5, limit, page)
        return fetch.call_args[0][0]

    def test_ddg_lite_uses_s_offset(self) -> None:
        self.assertIn("&s=10", self._url(websearch._e_ddg_lite, page=2))

    def test_bing_first_is_one_based(self) -> None:
        self.assertIn("&first=11", self._url(websearch._e_bing, page=2))

    def test_google_uses_start_offset(self) -> None:
        self.assertIn("&start=20", self._url(websearch._e_google, page=3))

    def test_yandex_page_is_zero_based(self) -> None:
        self.assertIn("&p=1", self._url(websearch._e_yandex, page=2))

    def test_yandex_page_1_has_no_param(self) -> None:
        self.assertNotIn("&p=", self._url(websearch._e_yandex, page=1))

    def test_github_page_is_one_based(self) -> None:
        self.assertIn("&p=2", self._url(websearch._e_github, page=2))

    def test_360_uses_pn_offset(self) -> None:
        self.assertIn("&pn=10", self._url(websearch._e_360, page=2))


class TestWikipediaEngine(unittest.TestCase):
    ZH_EMPTY = {"query": {"search": []}}
    EN_HIT = {"query": {"search": [{"title": "Foo Bar", "snippet": "<b>foo</b> 摘要"}]}}

    def test_empty_zh_falls_back_to_en(self) -> None:
        patcher, calls = patch_session(FakeResp(payload=self.ZH_EMPTY),
                                       FakeResp(payload=self.EN_HIT))
        with patcher:
            items = websearch._e_wikipedia("foo", 5, 10, 1)
        self.assertEqual(items[0]["url"], "https://en.wikipedia.org/wiki/Foo_Bar")
        self.assertEqual(len(calls), 2)

    def test_offset_comes_from_page(self) -> None:
        patcher, calls = patch_session(FakeResp(payload=self.EN_HIT))
        with patcher:
            websearch._e_wikipedia("foo", 5, 10, 3)
        self.assertEqual(calls[0][1]["params"]["sroffset"], 20)

    def test_non_200_raises(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=403))
        with patcher, self.assertRaises(websearch.SearchError):
            websearch._e_wikipedia("foo", 5, 10, 1)

    def test_both_languages_empty_returns_empty(self) -> None:
        patcher, _ = patch_session(FakeResp(payload=self.ZH_EMPTY),
                                   FakeResp(payload=self.ZH_EMPTY))
        with patcher:
            self.assertEqual(websearch._e_wikipedia("foo", 5, 10, 1), [])


class TestSogouEngine(unittest.TestCase):
    """搜狗:/link 跳转页要再 GET 一次才拿得到真 URL。"""

    HTML = ('<div class="vrwrap"><h3><a href="/link?url=abc">标题一</a></h3>'
            '<div class="text-layout">摘要一</div></div>')

    def test_meta_refresh_is_resolved(self) -> None:
        patcher, _ = patch_session(FakeResp(text="<script>window.location.replace(\"x\")</script>"
                                                 "URL='https://real.example/a'"))
        with patcher, mock.patch.object(websearch, "_fetch", return_value=self.HTML):
            items = websearch._e_sogou("q", 5, 10, 1)
        self.assertEqual(items[0]["url"], "https://real.example/a")

    def test_location_header_wins_over_body(self) -> None:
        patcher, _ = patch_session(FakeResp(text="URL='https://body.example/'",
                                            headers={"Location": "https://header.example/"}))
        with patcher, mock.patch.object(websearch, "_fetch", return_value=self.HTML):
            items = websearch._e_sogou("q", 5, 10, 1)
        self.assertEqual(items[0]["url"], "https://header.example/")

    def test_unresolvable_link_is_dropped(self) -> None:
        patcher, _ = patch_session(FakeResp(text="没有跳转信息"))
        with patcher, mock.patch.object(websearch, "_fetch", return_value=self.HTML):
            self.assertEqual(websearch._e_sogou("q", 5, 10, 1), [])

    def test_page_param_only_from_page_2(self) -> None:
        with mock.patch.object(websearch, "_fetch", return_value="") as fetch:
            websearch._e_sogou("q", 5, 10, 2)
        self.assertIn("&page=2", fetch.call_args[0][0])


class TestApiEngines(unittest.TestCase):
    """arXiv / Crossref / PubMed 三个官方 API 引擎。"""

    ATOM = ("<feed><entry><id>https://arxiv.org/abs/1</id><title>论文一</title>"
            "<summary>摘要一</summary></entry></feed>")

    def test_arxiv_parses_atom(self) -> None:
        patcher, calls = patch_session(FakeResp(text=self.ATOM))
        with patcher:
            items = websearch._e_arxiv("q", 5, 10, 2)
        self.assertEqual(items[0]["url"], "https://arxiv.org/abs/1")
        self.assertEqual(calls[0][1]["params"]["start"], 10)

    def test_arxiv_non_200_raises(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=500))
        with patcher, self.assertRaises(websearch.SearchError):
            websearch._e_arxiv("q", 5, 10, 1)

    def test_crossref_parses_items(self) -> None:
        payload = {"message": {"items": [{"title": ["论文"], "URL": "https://doi.example/1",
                                          "abstract": "<jats:p>摘要</jats:p>"}]}}
        patcher, calls = patch_session(FakeResp(payload=payload))
        with patcher:
            items = websearch._e_crossref("q", 5, 10, 2)
        self.assertEqual(items[0]["snippet"], "摘要")
        self.assertEqual(calls[0][1]["params"]["offset"], 10)

    def test_crossref_non_200_raises(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=502))
        with patcher, self.assertRaises(websearch.SearchError):
            websearch._e_crossref("q", 5, 10, 1)

    def test_pubmed_two_step_lookup(self) -> None:
        esearch = FakeResp(payload={"esearchresult": {"idlist": ["111"]}})
        esummary = FakeResp(payload={"result": {"111": {"title": "标题.", "source": "期刊"}}})
        patcher, _ = patch_session(esearch, esummary)
        with patcher:
            items = websearch._e_pubmed("q", 5, 10, 1)
        self.assertEqual(items, [{"url": "https://pubmed.ncbi.nlm.nih.gov/111/",
                                  "title": "标题", "snippet": "期刊"}])

    def test_pubmed_no_ids_returns_empty_without_second_call(self) -> None:
        patcher, calls = patch_session(FakeResp(payload={"esearchresult": {"idlist": []}}))
        with patcher:
            self.assertEqual(websearch._e_pubmed("q", 5, 10, 1), [])
        self.assertEqual(len(calls), 1)

    def test_pubmed_esearch_non_200_raises(self) -> None:
        patcher, _ = patch_session(FakeResp(status_code=429))
        with patcher, self.assertRaises(websearch.SearchError):
            websearch._e_pubmed("q", 5, 10, 1)

    def test_pubmed_esummary_non_200_raises(self) -> None:
        patcher, _ = patch_session(FakeResp(payload={"esearchresult": {"idlist": ["1"]}}),
                                   FakeResp(status_code=429))
        with patcher, self.assertRaises(websearch.SearchError):
            websearch._e_pubmed("q", 5, 10, 1)

    def test_pubmed_entry_without_title_is_dropped(self) -> None:
        patcher, _ = patch_session(
            FakeResp(payload={"esearchresult": {"idlist": ["1"]}}),
            FakeResp(payload={"result": {"1": {"title": "", "source": "x"}}}))
        with patcher:
            self.assertEqual(websearch._e_pubmed("q", 5, 10, 1), [])


class TestSearxEngine(unittest.TestCase):
    """_e_searx:逐个实例试,第一个出结果就停并把它挪到缓存最前。"""

    HIT = {"results": [{"url": "https://hit.example/1", "title": "命中", "content": "摘要"}]}

    def test_second_instance_wins_after_first_fails(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances",
                               return_value=["https://a/", "https://b/"]), \
             mock.patch.object(websearch, "_fetch_json",
                               side_effect=[OSError("403"), self.HIT]), \
             mock.patch.object(websearch, "_promote_searx") as promote, \
             redirect_stderr(io.StringIO()):
            items = websearch._e_searx("q", 5, 10, 1)
        self.assertEqual(items[0]["url"], "https://hit.example/1")
        promote.assert_called_once_with("https://b/", ["https://a/", "https://b/"])

    def test_empty_result_moves_on_to_next_instance(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances",
                               return_value=["https://a/", "https://b/"]), \
             mock.patch.object(websearch, "_fetch_json",
                               side_effect=[{"results": []}, self.HIT]), \
             mock.patch.object(websearch, "_promote_searx"), \
             redirect_stderr(io.StringIO()):
            items = websearch._e_searx("q", 5, 10, 1)
        self.assertEqual(len(items), 1)

    def test_all_instances_failing_raises_with_last_errors(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances",
                               return_value=["https://a/", "https://b/"]), \
             mock.patch.object(websearch, "_fetch_json",
                               side_effect=[OSError("timeout a"), OSError("timeout b")]), \
             self.assertRaises(websearch.SearchError) as cm:
            websearch._e_searx("q", 5, 10, 1)
        self.assertIn("timeout b", str(cm.exception))

    def test_no_instance_at_all_raises(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances", return_value=[]), \
             self.assertRaises(websearch.SearchError) as cm:
            websearch._e_searx("q", 5, 10, 1)
        self.assertIn("无可用实例", str(cm.exception))

    def test_pageno_is_forwarded(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances", return_value=["https://a/"]), \
             mock.patch.object(websearch, "_fetch_json", return_value=self.HIT) as fj, \
             mock.patch.object(websearch, "_promote_searx"), \
             redirect_stderr(io.StringIO()):
            websearch._e_searx("q", 5, 10, 4)
        self.assertEqual(fj.call_args[0][1]["pageno"], 4)


class TestCliEdges(unittest.TestCase):
    """list_engines / set_engines 的错误路径,以及 AI 环境的格式降级。"""

    def _run(self, fn, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = fn(*args)
        return rc, out.getvalue(), err.getvalue()

    def test_list_engines_survives_instance_load_failure(self) -> None:
        with mock.patch.object(websearch, "load_searx_instances",
                               side_effect=OSError("缓存读不了")):
            rc, out, err = self._run(websearch.list_engines)
        self.assertEqual(rc, 0)
        self.assertIn("ddg", out)
        self.assertIn("读取失败", err)

    def test_set_without_engines_keyword_is_rc_2(self) -> None:
        rc, _, err = self._run(websearch.set_engines, ["bogus"])
        self.assertEqual(rc, 2)
        self.assertIn("用法", err)

    def test_set_engines_without_args_prints_current(self) -> None:
        with mock.patch.object(websearch, "_active_engines", return_value=["ddg", "bing"]):
            rc, out, _ = self._run(websearch.set_engines, ["engines"])
        self.assertEqual(rc, 0)
        self.assertIn("ddg bing", out)

    def test_ai_env_downgrades_plain_to_tsv(self) -> None:
        results = [{"url": "https://e.example/1", "title": "标题", "snippet": "摘要"}]
        with mock.patch.object(websearch, "search", return_value=results), \
             mock.patch("lib.ai_env.is_ai_shell_env", return_value=True):
            rc, out, _ = self._run(websearch.main, ["websearch", "q"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("index\turl\ttitle\tsnippet"))



class TestCacheShapeGuard(SearxCacheCase):
    """缓存文件是 JSON 但形状不对（被别的程序写过、或旧格式）时也要能退回重新拉。"""

    PAYLOAD = {"instances": {"https://ok.example/": {
        "http": {"status_code": 200},
        "timing": {"search": {"success_percentage": 99, "all": {"value": 1}}}}}}

    def _load(self) -> list[str]:
        with mock.patch.object(websearch, "_fetch_json", return_value=self.PAYLOAD):
            return websearch.load_searx_instances()

    def test_json_array_cache_falls_back_to_refresh(self) -> None:
        self.cache.write_text("[]", encoding="utf-8")
        self.assertEqual(self._load(), ["https://ok.example/"])

    def test_dict_without_fetched_at_falls_back_to_refresh(self) -> None:
        self.cache.write_text('{"instances": ["https://old.example/"]}', encoding="utf-8")
        self.assertEqual(self._load(), ["https://ok.example/"])


class TestMainErrorPaths(unittest.TestCase):
    """main 的两条子命令分支也要接住 SearchError（配置里写错引擎名就会抛）。"""

    def _run(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.object(websearch, "load_config",
                                        return_value={"engines": ["没这个引擎"]}), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = websearch.main(argv)
        return rc, err.getvalue()

    def test_engines_subcommand_reports_the_bad_config(self) -> None:
        rc, err = self._run(["websearch", "engines"])
        self.assertEqual(rc, 2)
        self.assertIn("未知引擎", err)

    def test_set_engines_without_args_reports_the_bad_config(self) -> None:
        rc, err = self._run(["websearch", "set", "engines"])
        self.assertEqual(rc, 2)
        self.assertIn("未知引擎", err)


class TestMainDefaultArgv(unittest.TestCase):
    """main() 不传参数时要回落 sys.argv，而不是拿 None 去切片。"""

    def test_none_argv_falls_back_to_sys_argv(self) -> None:
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["websearch"]), \
             redirect_stdout(out):
            rc = websearch.main()
        self.assertEqual(rc, 0)
        self.assertIn("websearch skills", out.getvalue())


if __name__ == "__main__":
    unittest.main()
