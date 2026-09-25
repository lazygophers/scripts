"""webgrab 补测：直抓 / 渲染 / 登录三条外部依赖路径。

curl_cffi 和 playwright 都是重依赖，这里用假模块塞进 `sys.modules` 顶替——
既不连网也不起浏览器，只验 webgrab 怎么调它们（参数、顺序、清理）。
"""

import pathlib
import sys
import tempfile
import types
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import webgrab  # noqa: E402


def fake_playwright(ctx):
    """造一个 `playwright.sync_api` 假模块，sync_playwright() 产出带 chromium 的 p。"""
    p = mock.MagicMock()
    p.chromium.launch_persistent_context.return_value = ctx
    sync_playwright = mock.MagicMock()
    sync_playwright.return_value.__enter__.return_value = p
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = sync_playwright
    return p, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": module}


def fake_page(html: str = "<html>ok</html>"):
    page = mock.MagicMock()
    page.content.return_value = html
    return page


class TestFetchDirect(unittest.TestCase):
    """fetch_direct：curl_cffi Session 带指纹发一次 GET。"""

    def test_returns_status_and_text(self):
        response = mock.MagicMock(status_code=200, text="<html>hi</html>")
        session = mock.MagicMock()
        session.__enter__.return_value.get.return_value = response
        requests = mock.MagicMock()
        requests.Session.return_value = session
        module = types.ModuleType("curl_cffi")
        module.requests = requests

        with mock.patch.dict(sys.modules, {"curl_cffi": module}):
            got = webgrab.fetch_direct("https://a.com", 12.0, "safari")

        self.assertEqual(got, (200, "<html>hi</html>"))
        self.assertEqual(requests.Session.call_args.kwargs,
                         {"impersonate": "safari", "timeout": 12.0})
        get_kwargs = session.__enter__.return_value.get.call_args.kwargs
        self.assertEqual(get_kwargs["headers"], webgrab.EXTRA_HEADERS)
        self.assertIs(get_kwargs["allow_redirects"], True)


class TestLaunchContext(unittest.TestCase):
    """_launch_context：持久 profile 目录先建出来，再开 chromium。"""

    def test_creates_profile_dir_and_passes_headless(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = pathlib.Path(tmp) / "profile"
            p = mock.MagicMock()
            with mock.patch.object(webgrab, "PROFILE_DIR", profile):
                webgrab._launch_context(p, headless=True)
            self.assertTrue(profile.is_dir())
        self.assertEqual(p.chromium.launch_persistent_context.call_args.args[0], str(profile))
        self.assertEqual(p.chromium.launch_persistent_context.call_args.kwargs,
                         {"headless": True, "channel": "chromium"})


class RenderCase(unittest.TestCase):
    """基类：假 playwright + 临时 profile 目录。"""

    def setUp(self):
        self.ctx = mock.MagicMock()
        self.page = fake_page()
        self.ctx.pages = [self.page]
        self.p, modules = fake_playwright(self.ctx)
        for p in (mock.patch.dict(sys.modules, modules),
                  mock.patch.object(webgrab, "PROFILE_DIR",
                                    pathlib.Path(tempfile.mkdtemp()) / "profile")):
            p.start()
            self.addCleanup(p.stop)


class TestFetchRender(RenderCase):
    """fetch_render：渲染取 DOM，headed / scroll / wait 都落到 page 上。"""

    def test_returns_dom_and_closes_context(self):
        html = webgrab.fetch_render("https://a.com", 20)
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(self.page.goto.call_args.kwargs,
                         {"timeout": 20000, "wait_until": "domcontentloaded"})
        self.ctx.close.assert_called_once()

    def test_headed_means_not_headless(self):
        webgrab.fetch_render("https://a.com", 20, headed=True)
        self.assertIs(self.p.chromium.launch_persistent_context.call_args.kwargs["headless"], False)

    def test_networkidle_timeout_is_swallowed(self):
        # 长连接站点永远到不了 networkidle，等不齐不算失败
        self.page.wait_for_load_state.side_effect = RuntimeError("timeout")
        self.assertEqual(webgrab.fetch_render("https://a.com", 20), "<html>ok</html>")

    def test_scroll_wheels_once_per_round(self):
        webgrab.fetch_render("https://a.com", 20, scroll=3)
        self.assertEqual(self.page.mouse.wheel.call_count, 3)
        self.page.mouse.wheel.assert_called_with(0, 2000)

    def test_extra_wait_is_milliseconds(self):
        webgrab.fetch_render("https://a.com", 20, wait=1.5)
        self.page.wait_for_timeout.assert_called_with(1500)

    def test_new_page_when_context_has_none(self):
        self.ctx.pages = []
        self.ctx.new_page.return_value = fake_page("<html>new</html>")
        self.assertEqual(webgrab.fetch_render("https://a.com", 20), "<html>new</html>")

    def test_context_closed_even_when_goto_raises(self):
        self.page.goto.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            webgrab.fetch_render("https://a.com", 20)
        self.ctx.close.assert_called_once()


class TestLogin(RenderCase):
    """login：开可见浏览器，等用户回车后关掉，cookie 留在 profile 里。"""

    def test_opens_headed_browser_and_waits_for_enter(self):
        with mock.patch("builtins.input", return_value="") as entered, \
             mock.patch("builtins.print"):
            rc = webgrab.login("https://a.com")
        self.assertEqual(rc, 0)
        entered.assert_called_once()
        self.assertIs(self.p.chromium.launch_persistent_context.call_args.kwargs["headless"], False)
        self.page.goto.assert_called_once_with("https://a.com")
        self.ctx.close.assert_called_once()

    def test_uses_new_page_when_context_empty(self):
        self.ctx.pages = []
        with mock.patch("builtins.input", return_value=""), mock.patch("builtins.print"):
            webgrab.login("https://a.com")
        self.ctx.new_page.assert_called_once()


class TestToMarkdown(unittest.TestCase):
    """to_markdown：script/style 等非正文元素连内容一起删。"""

    def test_script_and_style_content_are_dropped(self):
        html = ("<html><head><title>t</title></head><body>"
                "<script>var secret=1</script><style>.a{color:red}</style>"
                "<h1>标题</h1></body></html>")
        got = webgrab.to_markdown(html)
        self.assertIn("标题", got)
        self.assertNotIn("secret", got)
        self.assertNotIn("color:red", got)


if __name__ == "__main__":
    unittest.main()
