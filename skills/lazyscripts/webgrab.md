## webgrab

抓网页转 Markdown（默认打 stdout，不落盘）。三层反爬递进：

1. curl_cffi 模拟浏览器 TLS 指纹直抓（过大部分基础反爬 / CF 静态拦截），被拦自动换指纹重试
2. 仍被拦则回退 Playwright 无头 Chromium 渲染，拿 JS 跑完后的最终 DOM
3. 交互式 Turnstile / 人机验证不会自动点过，如实报错退出

```bash
webgrab https://example.com            # Markdown → stdout
webgrab <url> -o page.md               # 存文件
webgrab <url> --html                   # 保留原始 HTML
webgrab <url> --render                 # 跳过直抓，强制 Playwright 渲染（JS 页用）
webgrab <url> --timeout 60             # 慢站调超时（默认 30s）
```
