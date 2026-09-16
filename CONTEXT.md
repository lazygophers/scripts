# CONTEXT

本仓领域词汇表。只放词与定义，不放实现细节。

## graphwatch

- **注册表（registry）**：被监听目录的清单，add/remove 读写，daemon 热加载。
- **补课（catchup）**：daemon 启动或热加载新目录时，发现图谱落后于源码就入队重建的行为。只在挂载那一刻发生一次，之后靠监听。
- **新鲜度（freshness）**：单目录图谱是否落后于源码的状态。四态：新鲜 / 未构建 / 过期 / 更新中。daemon 在跑且源码有变更 = 更新中；没跑才是过期。
- **监听与重建分离**：watchdog 线程只负责发现变更（轻），重建走全局队列由 worker 执行（重）。同一目录的变更在队列里去重合并。
- **重建并发（rebuild concurrency）**：同时执行重建的目录数，最小 1，默认 1（串行）。
- **服务管理（service management）**：把 daemon 注册成用户级系统服务的动作集合——install / uninstall / start / stop / restart / registered / state。与 daemon 内部逻辑互不感知。
- **平台 adapter**：服务管理在某平台的具体实现（macOS launchd / Linux systemd --user / Windows schtasks）。平台差异只存在于 adapter 内部。

## browse（浏览器控制）

- **context（上下文）**：一个可被指令作用的浏览上下文，即一个标签页或其中一个 frame。id 约定：顶层标签页是 `"<tabId>"`，子 frame 是 `"<tabId>.<frameId>"`。选哪个 context 的优先级恒为 `--context <id>` > `--match-url '<glob>'`（命中多个就报错要求收窄）> 当前活动标签页。
- **element（元素）**：context 里的一个 DOM 节点，指令的作用对象。永远由 locator 现场解析得到，不在进程间传递句柄。
- **locator（定位器）**：一条描述「找哪个元素」的字符串，四种 scheme 前缀分派——`css=` / `text=`（`text*=` 为包含匹配）/ `xpath=` / `js=`，不写前缀默认 `css=`。`js=` 在 MAIN world 求值，是唯一能穿透 shadow DOM 的方式，也因此算高危动作。默认自动等待最多 5 秒直到元素出现且可交互。
- **daemon（常驻中转）**：本机一个 Unix socket 服务端，站在 CLI 与浏览器之间。存在的理由：浏览器只会 fork native host，而用户敲命令的 CLI 是另一个进程，两者天生连不上。只做传输与路由，不懂指令语义。
- **native host（本机宿主）**：浏览器按 native messaging 协议 fork 起来的那个进程，实为 `browse --native-host`。一头是浏览器给的 stdin/stdout 管道，一头是 daemon 的 socket，只搬运不解释。
- **command（指令）**：一条 WebDriver BiDi 形状的请求信封 `{id, method, params}`，`method` 写成 `<module>.<action>`（私有能力带 `lg:` 前缀，如 `lg:history.search`）。回包只有 Success / Error 两种，错误码用 BiDi 标准枚举或带冒号的扩展码。

## viewer（本地文件展示）

- **纯文本页（plain-text page）**：浏览器没有当成网页渲染、只是把文件原文倒出来的那一类页面。判定三件事同时成立：`document.contentType` 是纯文本类、`body` 下恰好一个 `<pre>`、`document.title` 为空。不限 `file://`，任何来源都算。
- **美化（prettify）**：把一张纯文本页换成按文件类型渲染过的样子——markdown 渲染、代码高亮、json/yaml 折叠树、csv 表格、log 分列上色。反面是**原始（raw）**，即那张纯文本页本来的样子。两者随时可切，切换状态不跨页记忆。
- **接管（takeover）**：viewer 决定对某一页做美化。三档：白名单内的文件类型直接接管；明确是网页的不碰；剩下拿不准的不接管，只浮一个「美化一下」按钮交给人点。
- **方言（dialect）**：markdown 的一种扩展写法。本仓认七种：GFM、front-matter、Obsidian 双括号链接、脚注、`:::` 提示框、Pandoc、MDX。MDX 只做降级渲染——它要编译成 JavaScript 再执行，而扩展禁止执行运行时生成的代码。
- **降级渲染（degraded render）**：某种语法做不到完整效果时，仍按普通 markdown 渲染正文，在做不到的那个位置留一个写明原因的占位块。信息不丢，是「这里本来有东西」的痕迹。
- **下载（download）**：浏览器判定某文件类型不可内联，转而存盘。存盘的页面根本不存在，viewer 无从接管。默认不管已被下载的类型；**强制拦截（force intercept）**是一个用户自己开的开关，开了就在导航发生前把这类文件改跳到 viewer 自己的页面。
- **file 权限开关**：`chrome://extensions` 里那个「允许访问文件网址」。扩展声明 `host_permissions` 不够，必须用户手动打开，运行时可用 `chrome.extension.isAllowedFileSchemeAccess()` 读到真实状态。

## git 工作流

- **分支会话（branch session）**：切到目标分支（不存在则建跟踪分支）并在退出时按路径决定是否回原分支的保证。错误路径默认回滚，成功路径默认留在目标分支。回滚幂等：已在原分支则跳过。
