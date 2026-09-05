"""webgrab — 抓网页存成 HTML 文件。

curl_cffi 模拟浏览器 TLS 指纹直抓（过大部分基础反爬 / CF 静态拦截）；
被拦时自动换指纹重试，仍被拦则回退 Playwright 真浏览器渲染拿最终 DOM。
交互式 Turnstile / 人机验证不会自动点过，只如实报错。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

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


def fetch_render(url: str, timeout: float) -> str:
    """Playwright 无头 Chromium 渲染，返回最终 DOM HTML。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            # networkidle 等不齐就跳过：很多站点有长连接，永远到不了 idle
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return page.content()
        finally:
            browser.close()


def grab(url: str, timeout: float = 30, force_render: bool = False) -> tuple[str, str]:
    """抓取并处理反爬，返回 (HTML, 来源描述)。全部路径失败时抛 GrabError。"""
    if force_render:
        return fetch_render(url, timeout), "playwright 渲染"

    last = ""
    for imp in IMPERSONATE_QUEUE:
        status, html = fetch_direct(url, timeout, imp)
        if not is_blocked(status, html):
            return html, f"curl_cffi({imp})"
        last = f"指纹 {imp} 被拦 (HTTP {status})"
        print(f"[webgrab] {last}，换下一个", file=sys.stderr)

    print("[webgrab] 直抓全被拦，回退 Playwright 渲染", file=sys.stderr)
    html = fetch_render(url, timeout)
    if is_blocked(200, html):
        raise GrabError(
            "Playwright 渲染后仍是验证页（交互式 Turnstile/人机验证无法自动通过）"
        )
    return html, "playwright 渲染"


class GrabError(RuntimeError):
    pass


def default_output(url: str) -> Path:
    """默认输出文件名：<域名>.html，存当前目录。"""
    host = urlparse(url).netloc or "page"
    return Path(f"{host}.html")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="webgrab",
        description="抓网页存成 HTML 文件（curl_cffi 指纹直抓，被拦自动回退 Playwright 渲染）",
        epilog="交互式 Turnstile / 人机验证不会自动点过，只如实报错。",
    )
    p.add_argument("url", help="要抓的网址")
    p.add_argument("-o", "--output", help="输出文件（默认 <域名>.html 存当前目录）")
    p.add_argument("--stdout", action="store_true", help="打到 stdout 而不是写文件")
    p.add_argument("--render", action="store_true", help="跳过直抓，强制 Playwright 渲染（JS 渲染页用）")
    p.add_argument("--timeout", type=float, default=30, help="超时秒数（默认 30）")
    return p


def main(argv: list[str] | None = None) -> int:
    # argv 传 sys.argv 全量（与 cpd 约定一致），None 时 argparse 自取
    args = build_parser().parse_args(argv[1:] if argv is not None else None)
    try:
        html, source = grab(args.url, timeout=args.timeout, force_render=args.render)
    except GrabError as e:
        print(f"[webgrab] 抓取失败: {e}", file=sys.stderr)
        return 1

    if args.stdout:
        sys.stdout.write(html)
    else:
        out = Path(args.output) if args.output else default_output(args.url)
        out.write_text(html, encoding="utf-8")
        print(f"[webgrab] {len(html)} 字节 ← {source} → {out}", file=sys.stderr)
    return 0
