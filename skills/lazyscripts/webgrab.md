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

## 站点支持

34 个内置站点（小红书、B站、知乎、微博、抖音、微信公众号、掘金、CSDN、豆瓣、x/twitter、YouTube、notion 等）按域名自动套配置：强制渲染、滚动触发懒加载、额外等待，无需传参。`--scroll N` 手动控制滚动屏数。

登录墙（要登录但可跳过/需要登录态）：

```bash
webgrab login https://www.xiaohongshu.com   # 弹浏览器手动登录一次，cookie 存进持久 profile
webgrab https://www.xiaohongshu.com/explore/x  # 之后抓取自动带登录态
```

要手动过滑块验证：加 `--headed` 显示浏览器窗口。
