# viewer 用静态 content script 注入 `<all_urls>`

一个「本地文件查看器」申请 `<all_urls>` 看起来过宽，所以记下为什么。viewer 要接管的是**纯文本页**这一形态，而不是 `file://` 这一来源：GitHub raw、内网日志、任何直接吐 `text/plain` 的 URL 都同样值得美化，用户第 1 轮明确要求不限 `file://`。上游两种做法都有实证——`simov/markdown-viewer` 完全不声明 `content_scripts`，靠 service worker 在 `tabs.onUpdated` 里动态 `executeScript`；`callumlocke/json-formatter` 用静态 `content_scripts` + `<all_urls>` + `run_at: document_start`。选后者，理由是动态注入必然晚于首帧，`<pre>` 会先闪出来再被替换，上游为此不得不先把 `<pre>` 设成 `visibility:hidden` 再注入，是在补自己造的洞。静态注入在 `document_start` 就位，没有这一帧。

代价是 content script 会在每个页面上跑一次。约束写死在脚本第一行：判定不是纯文本页就立即 return，不碰 DOM、不加载任何渲染依赖（highlight.js / Mermaid / KaTeX 全部懒加载）。

## Consequences

安装时用户会看到「读取和更改您在所有网站上的数据」的权限提示，这是这条决策的直接代价，无法通过 `optional_host_permissions` 回避（那会让首次访问每个域名都弹一次）。另外 `file://` 上的注入还额外需要用户在 `chrome://extensions` 手动打开「允许访问文件网址」，声明权限不够，所以安装后要弹一次欢迎页引导。
