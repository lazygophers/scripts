"""webgrab — 抓网页转 Markdown 打到 stdout（-o 才落盘）。

curl_cffi 模拟浏览器 TLS 指纹直抓（过大部分基础反爬 / CF 静态拦截）；
被拦时自动换指纹重试，仍被拦则回退 Playwright 真浏览器渲染拿最终 DOM。
默认 HTML 转 Markdown（markdownify）；--html 保留原始 HTML。
交互式 Turnstile / 人机验证不会自动点过，只如实报错。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from pathlib import Path
from urllib.parse import urlparse

# 持久浏览器 profile：login 子命令存 cookie，之后抓取自动复用（小红书/B站等
# 「要登录但可跳过」站点靠它过风控）。独立于用户 Chrome profile，无锁库风险。
PROFILE_DIR = pathlib.Path.home() / ".config/lazygophers/scripts/webgrab-profile"

# TLS 指纹轮换队列：一个被拦换下一个
IMPERSONATE_QUEUE = ("chrome", "safari", "edge")

# 反爬拦截页特征（CF / 各家 WAF 的共同标记，小写匹配）
BLOCK_MARKERS = (
    "just a moment",            # Cloudflare 挑战页
    "cf-mitigated",             # Cloudflare 响应头/标记
    "checking your browser",    # CF 旧版 / DDoS-Guard
    "attention required",       # CF 阻断页
    "verify you are human",     # 通用验证页
    "captcha-delivery",         # hCaptcha
    "ddos protection by",       # DDoS-Guard
)

BLOCK_STATUS = (403, 429, 503)

# 站点特殊配置：域名后缀 → 该站点抓取要走的路径/参数。
# render=True 强制 Playwright（直抓必被拦或内容是 JS 拼的）；
# scroll=N 渲染后滚动 N 屏触发懒加载（feed 流页面内容靠滚动才出现）；
# wait=N 渲染后额外等 N 秒（慢站点拼内容）。
SITE_CONFIG: dict[str, dict] = {
    "xiaohongshu.com": {"render": True, "scroll": 3, "wait": 2},
    "xhslink.com": {"render": True, "scroll": 3, "wait": 2},
    "bilibili.com": {"render": True, "wait": 1},
    "b23.tv": {"render": True, "wait": 1},
    "zhihu.com": {"render": True, "wait": 2},
    "zhuanlan.zhihu.com": {"render": True, "wait": 2},
    "weibo.com": {"render": True, "wait": 2},
    "weibo.cn": {"render": True, "wait": 2},
    "douyin.com": {"render": True, "scroll": 3, "wait": 2},
    "mp.weixin.qq.com": {"render": True, "wait": 1},
    "juejin.cn": {"render": True, "wait": 1},
    "tieba.baidu.com": {"render": True, "wait": 1},
    "douban.com": {"render": True, "wait": 1},
    "36kr.com": {"render": True, "wait": 1},
    "sspai.com": {"render": True},
    "ithome.com": {"render": True},
    "infoq.cn": {"render": True, "wait": 2},
    "jianshu.com": {"render": True, "wait": 1},
    "csdn.net": {"render": True, "wait": 1},
    "cnblogs.com": {"render": True},
    "x.com": {"render": True, "wait": 2},
    "twitter.com": {"render": True, "wait": 2},
    "instagram.com": {"render": True, "wait": 2},
    "youtube.com": {"render": True, "wait": 2},
    "youtu.be": {"render": True, "wait": 2},
    "reddit.com": {"render": True, "wait": 1},
    "medium.com": {"render": True, "wait": 1},
    "substack.com": {"render": True, "wait": 1},
    "notion.site": {"render": True, "wait": 3},
    "notion.so": {"render": True, "wait": 3},
    "linkedin.com": {"render": True, "wait": 2},
    "taobao.com": {"render": True, "wait": 2},
    "tmall.com": {"render": True, "wait": 2},
    "jd.com": {"render": True, "wait": 2},
}


def site_config(url: str) -> dict:
    """URL 命中的站点配置（最长后缀优先），未命中返回空 dict。"""
    host = urlparse(url).netloc.lower()
    for suffix in sorted(SITE_CONFIG, key=len, reverse=True):
        if host == suffix or host.endswith("." + suffix):
            return SITE_CONFIG[suffix]
    return {}

# 直抓请求头：只补 Accept-Language / Referer，UA 由 curl_cffi 按指纹自带
EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def is_blocked(status: int, html: str) -> bool:
    """响应是否命中反爬拦截（状态码或页面特征任一命中）。"""
    if status in BLOCK_STATUS:
        return True
    low = html[:20000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def fetch_direct(url: str, timeout: float, impersonate: str) -> tuple[int, str]:
    """curl_cffi 直抓，返回 (状态码, HTML)。"""
    from curl_cffi import requests

    with requests.Session(impersonate=impersonate, timeout=timeout) as s:
        r = s.get(url, headers=EXTRA_HEADERS, allow_redirects=True)
        return r.status_code, r.text


def _launch_context(p, *, headless: bool):
    """起持久 profile 的 Chromium context（cookie 跨次复用）。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless, channel="chromium"
    )


def fetch_render(url: str, timeout: float, headed: bool = False, scroll: int = 0, wait: float = 0) -> str:
    """Playwright Chromium 渲染，返回最终 DOM HTML。

    channel="chromium" 用完整版浏览器的 new headless，无需额外下载 headless shell。
    持久 profile 让 login 存下的 cookie 自动生效（小红书/B站等站点）。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=not headed)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            # networkidle 等不齐就跳过：很多站点有长连接，永远到不了 idle
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            if scroll:
                for _ in range(scroll):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(500)
            if wait:
                page.wait_for_timeout(int(wait * 1000))
            return page.content()
        finally:
            ctx.close()


def login(url: str) -> int:
    """开可见浏览器到 url，手动登录/过验证后回车，cookie 存进持久 profile。

    用法: webgrab login <url>
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        print(f"[webgrab] 在浏览器里完成登录/验证后，回到终端按回车（profile: {PROFILE_DIR}）")
        input()
        ctx.close()
    print("[webgrab] cookie 已保存，后续抓取自动复用")
    return 0


def grab(url: str, timeout: float = 30, force_render: bool = False, headed: bool = False, scroll: int = 0, wait: float = 0) -> tuple[str, str]:
    """抓取并处理反爬，返回 (HTML, 来源描述)。全部路径失败时抛 GrabError。"""
    if force_render:
        return fetch_render(url, timeout, headed=headed, scroll=scroll, wait=wait), "playwright 渲染"

    last = ""
    for imp in IMPERSONATE_QUEUE:
        status, html = fetch_direct(url, timeout, imp)
        if not is_blocked(status, html):
            return html, f"curl_cffi({imp})"
        last = f"指纹 {imp} 被拦 (HTTP {status})"
        print(f"[webgrab] {last}，换下一个", file=sys.stderr)

    print("[webgrab] 直抓全被拦，回退 Playwright 渲染", file=sys.stderr)
    html = fetch_render(url, timeout, headed=headed, scroll=scroll, wait=wait)
    if is_blocked(200, html):
        raise GrabError(
            "Playwright 渲染后仍是验证页（交互式 Turnstile/人机验证无法自动通过）"
        )
    return html, "playwright 渲染"


class GrabError(RuntimeError):
    pass


def to_markdown(html: str) -> str:
    """HTML → Markdown：先删 script/style 等非正文元素（连内容），再转换。"""
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["head", "script", "style", "noscript", "template"]):
        tag.decompose()
    return md(str(soup), heading_style="ATX").strip() + "\n"


def default_output(url: str, suffix: str) -> Path:
    """默认输出文件名：<域名>.<suffix>，存当前目录。"""
    host = urlparse(url).netloc or "page"
    return Path(f"{host}.{suffix}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="webgrab",
        description="抓网页转 Markdown 打 stdout（curl_cffi 指纹直抓，被拦自动回退 Playwright 渲染）",
        epilog="交互式 Turnstile / 人机验证不会自动点过，只如实报错。",
    )
    p.add_argument("url", help="要抓的网址")
    p.add_argument("-o", "--output", help="写文件而不是打 stdout（默认 <域名>.md / --html 时 <域名>.html）")
    p.add_argument("--html", action="store_true", help="保留原始 HTML，不转 Markdown")
    p.add_argument("--render", action="store_true", help="跳过直抓，强制 Playwright 渲染（JS 渲染页用）")
    p.add_argument("--headed", action="store_true", help="渲染时显示浏览器窗口（要手动过滑块验证时用）")
    p.add_argument("--scroll", type=int, default=0, help="渲染后滚动 N 屏触发懒加载（feed 流页面用，0=不滚）")
    p.add_argument("--timeout", type=float, default=None, help="超时秒数（默认 30；站点配置可覆盖）")
    return p


def main(argv: list[str] | None = None) -> int:
    # argv 传 sys.argv 全量（与 cpd 约定一致），None 时 argparse 自取
    rest = argv[1:] if argv is not None else None
    # login 子命令走交互流程，不进 argparse（要 input()）
    if rest and rest[0] == "login":
        if len(rest) < 2:
            print("用法: webgrab login <url>", file=sys.stderr)
            return 2
        return login(rest[1])
    args = build_parser().parse_args(rest)
    # 站点配置只填用户没显式给的参数（None/0 = 未指定）
    cfg = site_config(args.url)
    if cfg:
        if cfg.get("render") and not args.render:
            args.render = True
            print(f"[webgrab] 站点配置命中: 强制渲染 + {cfg}", file=sys.stderr)
        args.scroll = args.scroll or cfg.get("scroll", 0)
    timeout = args.timeout or cfg.get("timeout") or 30
    wait = cfg.get("wait", 0)
    try:
        html, source = grab(args.url, timeout=timeout, force_render=args.render,
                            headed=args.headed, scroll=args.scroll, wait=wait)
    except GrabError as e:
        print(f"[webgrab] 抓取失败: {e}", file=sys.stderr)
        return 1

    if args.html:
        content = html
        suffix = "html"
    else:
        content = to_markdown(html)
        suffix = "md"

    if args.output:
        out = Path(args.output)
        out.write_text(content, encoding="utf-8")
        print(f"[webgrab] {len(content)} 字节 ← {source} → {out}", file=sys.stderr)
    else:
        sys.stdout.write(content)
    return 0
