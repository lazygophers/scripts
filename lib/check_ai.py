"""AI API 端点连通性检测（空 POST，无任何密钥；HTTP 状态码 / TTFB / 断流）。"""

from __future__ import annotations

import argparse
import os
import time

from lib.exec import CommandTimeout, run
from lib.ui import reporter

# 预置端点：名称 → 官方 URL。一律不读环境密钥/base_url（防账号触发风控），
# 空 POST 无 key 探测，4xx 即视为可达。
ENDPOINTS = {
    "claude": "https://api.anthropic.com/v1/messages",
    "codex": "https://api.openai.com/v1/responses",
    "openai": "https://api.openai.com/v1/chat/completions",
    "glm": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "kimi": "https://api.moonshot.cn/v1/chat/completions",
    "minimax": "https://api.minimax.chat/v1/text/chatcompletion_v2",
}

CURL_FORMAT = "%{http_code} %{time_starttransfer} %{time_total} %{num_connects} %{size_download}"

# curl 中途断流类退出码：18=传输中断 56=recv 失败 → 对应 "Connection lost mid-response"
MIDSTREAM_EXIT = {18, 56}


def build_probe(target: str) -> dict:
    """解析目标 → {url, headers, body, mode}。一律空 POST 模式，不带任何密钥。"""
    key = target.lower()
    if key in ENDPOINTS:
        return _empty(ENDPOINTS[key])
    if "://" in target:
        return _empty(target)
    raise ValueError(f"未知目标 {target!r}，可选: {'/'.join(ENDPOINTS)} 或直接传 URL")


def _empty(url: str) -> dict:
    return {"url": url, "headers": {}, "body": "{}", "mode": "空POST连通"}


def default_proxy() -> str | None:
    """从环境取代理（HTTPS_PROXY 优先，小写兜底）。"""
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )


def probe_once(url: str, *, timeout: float, proxy: str | None,
               headers: dict | None = None, body: str = "{}") -> dict:
    """单次 curl 探测。返回 {ok, http_code, ttfb, total, connects, size, error}。

    ok = curl 完整跑完（含响应体收完）且 http_code != 000。
    curl exit 18/56 = 响应中途断流（mid-response drop）。
    """
    cmd = ["curl", "-sS", "-N", "-o", "/dev/null", "-m", str(timeout), "-w", CURL_FORMAT]
    if proxy:
        cmd += ["-x", proxy]
    cmd += [url, "-H", "content-type: application/json"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd += ["-d", body]
    result = {"ok": False, "http_code": "000", "ttfb": None, "total": None,
              "connects": None, "size": None, "error": ""}
    try:
        p = run(cmd, capture_output=True, timeout=timeout + 5)
    except CommandTimeout as e:
        result["error"] = str(e)
        return result
    if p.returncode in MIDSTREAM_EXIT:
        result["error"] = f"响应中断 mid-response drop (curl exit={p.returncode})"
        return result
    if p.returncode != 0:
        stderr = (p.stderr or "").strip()
        result["error"] = stderr.splitlines()[-1] if stderr else f"curl exit={p.returncode}"
        return result
    try:
        code, ttfb, total, connects, size = (p.stdout or "").split()
        result.update(http_code=code, ttfb=float(ttfb), total=float(total),
                      connects=int(connects), size=int(size))
        result["ok"] = code != "000"
    except ValueError:
        result["error"] = f"curl 输出无法解析: {p.stdout!r}"
    return result


def check(target: str, *, count: int, infinite: bool, timeout: float,
          interval: float, proxy: str | None) -> int:
    """循环探测并输出汇总。返回进程退出码（全通 0 / 有失败 1）。"""
    r = reporter(stderr=True)
    probe = build_probe(target)
    label_count = "∞" if infinite else str(count)

    r.rule("AI 端点检测", style="blue")
    r.kv("任务", {
        "目标": target,
        "URL": probe["url"],
        "模式": probe["mode"],
        "次数": label_count,
        "超时": f"{timeout:g}s",
        "间隔": f"{interval:g}s",
        "代理": proxy or "（无）",
    })

    ok_count = 0
    fail_count = 0
    ttfbs: list[float] = []
    codes: dict[str, int] = {}
    interrupted = False
    i = 0

    try:
        while infinite or i < count:
            i += 1
            res = probe_once(probe["url"], timeout=timeout, proxy=proxy,
                             headers=probe["headers"], body=probe["body"])
            label = f"[{i}/{label_count}]"
            if res["ok"]:
                ok_count += 1
                ttfbs.append(res["ttfb"])
                codes[res["http_code"]] = codes.get(res["http_code"], 0) + 1
                r.ok(f"{label} 网络 正常 · HTTP {res['http_code']} · TTFB {res['ttfb']:.3f}s"
                     f" · 总耗时 {res['total']:.3f}s · 收 {res['size']}B")
            else:
                fail_count += 1
                codes[res["http_code"]] = codes.get(res["http_code"], 0) + 1
                detail = res["error"] or f"HTTP {res['http_code']}"
                r.err(f"{label} 网络 被禁用 · {detail}")
            if infinite or i < count:
                time.sleep(interval)
    except KeyboardInterrupt:
        interrupted = True

    code_dist = " ".join(f"{c}×{n}" for c, n in sorted(codes.items())) or "（无响应）"
    rows = [
        ("总次数", str(i), None),
        ("网络", f"正常×{ok_count} / 被禁用×{fail_count}",
         "green" if fail_count == 0 else "red"),
        ("成功", str(ok_count), "green" if ok_count else None),
        ("失败", str(fail_count), "red" if fail_count else None),
        ("状态码", code_dist, None),
    ]
    if ttfbs:
        rows.append(("TTFB avg/min/max",
                     f"{sum(ttfbs) / len(ttfbs):.3f}s / {min(ttfbs):.3f}s / {max(ttfbs):.3f}s",
                     None))
    r.rule("检测结果", style="green" if fail_count == 0 else "red")
    r.summary("", rows)
    if interrupted:
        r.warn("用户中断，以上为已完成部分")
    return 0 if fail_count == 0 and not interrupted else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_ai",
        description="AI API 端点连通性检测（空 POST 无密钥；HTTP 状态码 / TTFB / 断流）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  check_ai claude                      # 默认检测 5 次，间隔 5s\n"
               "  check_ai kimi -n 20 --interval 10    # 20 次，间隔 10s\n"
               "  check_ai openai -t 5                 # 单次超时 5s\n"
               "  check_ai glm -i                      # 不间断检测（Ctrl+C 结束并出汇总）\n"
               "  check_ai https://api.x.ai/v1 -n 3    # 直接指定 URL（空 POST 连通性）\n"
               "  check_ai claude --proxy http://127.0.0.1:7890",
    )
    parser.add_argument("target", help="端点名 (%s) 或完整 URL" % "/".join(ENDPOINTS))
    parser.add_argument("-n", "--count", type=int, default=5, help="检测次数（默认 5）")
    parser.add_argument("-i", "--infinite", action="store_true", help="不间断检测（Ctrl+C 结束）")
    parser.add_argument("-t", "--timeout", type=float, default=15.0,
                        help="单次请求超时秒数（默认 15）")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="检测间隔秒数（默认 5）")
    parser.add_argument("--proxy", default=None,
                        help="代理地址（默认取 HTTPS_PROXY/HTTP_PROXY 环境变量）")
    # 无参数时输出完整帮助（而非 argparse 的单行报错）
    if argv is not None and len(argv) <= 1:
        parser.print_help()
        return 2
    args = parser.parse_args(argv[1:] if argv else None)

    try:
        build_probe(args.target)
    except ValueError as e:
        reporter(stderr=True).err(str(e))
        return 2

    return check(
        args.target,
        count=args.count,
        infinite=args.infinite,
        timeout=args.timeout,
        interval=args.interval,
        proxy=args.proxy or default_proxy(),
    )
