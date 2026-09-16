# browse 浏览器扩展

`browse` 的浏览器端。通过 Native Messaging 连本机 daemon（host 名 `com.lazygophers.browse`），
收 WebDriver BiDi 形状的指令，执行后回结果。方案见 `.scratch/browser-control-extension/spec.md`。

## 安装（Chrome / Edge / Brave，四步 + 三个可选开关）

方案第 7.1 节。**不走 Chrome Web Store，自己分发，手动更新。**

### 第 1 步：构建出可加载的扩展目录

```bash
cd browser-extension/browse
npm install
npm run build      # 产出 dist/，这就是要加载的那个目录
```

`dist/` 的绝对路径下一步要用，先 `pwd` 记下来：`<仓库>/browser-extension/browse/dist`。

### 第 2 步：打开「开发者模式」，**此后一直开着**

浏览器地址栏输 `chrome://extensions`（Edge 是 `edge://extensions`，Brave 是
`brave://extensions`），把右上角的「开发者模式」开关打开。

**这个开关必须一直开着。** Chrome 134 起，关掉它会直接**禁用**所有以「已解压」方式
加载的扩展（内部原因码 `DISABLE_UNSUPPORTED_DEVELOPER_EXTENSION`）——不是隐藏，是
停止工作。关掉再打开需要重新启用扩展。

### 第 3 步：「加载已解压的扩展程序」，选第 1 步的 `dist/`

加载成功后，扩展卡片上会显示 ID。它应该恒等于：

```
podeceeeafjdcemppcgjhhokcokpcama
```

对不上就是 `manifest.json` 的 `key` 字段被改过或丢了——**ID 不对，第 4 步注册的
manifest 就会失配，扩展永远连不上 daemon**。详见下面「扩展 ID 与签名密钥」。

### 第 3 步之后：那三个开关，按需自己点（可选）

加载完之后，扩展的「详情」页里有三个开关。**一个都不是必做**，按需要点，各点一次就长期
有效：

| 开关 | 什么情况下才需要开 |
|---|---|
| 固定到工具栏 | 想让图标一直露在地址栏右边，不用每次去「扩展」菜单里翻 |
| 允许访问文件网址 | 只有要操作 `file://` 开头的本地文件时才需要 |
| 在无痕模式下启用 | 只有要在无痕（隐身）窗口里干活时才需要 |

**这三个只能你自己点，`browse install` 不会替你点，也不应该替你点。** 前两个要写浏览器的
企业策略才能程序化设置，而那等于改浏览器自身的配置；第三个连策略字段都不存在。

「无痕那条没有策略字段」不是推断，是正面证据：`ExtensionSettings` 策略按扩展 ID 配置时，
schema 允许的字段**全集**只有这 11 个 ——

```
allowed_permissions, blocked_install_message, blocked_permissions,
file_url_navigation_allowed, installation_mode, minimum_version_required,
override_update_url, runtime_allowed_hosts, runtime_blocked_hosts,
toolbar_pin, update_url
```

里面没有 `incognito`。出处是 Chromium 的策略定义本身（比文档页更硬）：
<https://chromium.googlesource.com/chromium/src/+/main/components/policy/resources/templates/policy_definitions/Extensions/ExtensionSettings.yaml>

### 第 4 步：注册 native host manifest

```bash
browse install            # 装
browse install --list     # 只看会写哪些文件，不动盘
browse uninstall          # 卸
```

它是 `browse` 的子命令（实现在 `lib/browse_install.py`），跟着 `browse` 一起发布，
所以 `uvx` / `uv tool install` 装来的那份也有。

**先把 `browse` 装成常驻命令再跑 `install`。** manifest 里写死的是浏览器每次启动都要
去 fork 的绝对路径，而 `uvx` 那种一次性环境的路径随时会消失。`browse install` 找路径
的顺序是：`--browse-path` > PATH 上的 `browse`（`uv tool install` / `pipx` 的落点）>
仓库里的 `bin/browse`。

**装完必须重启浏览器。** 浏览器只在启动时扫一遍 native messaging 的 manifest 目录，
不重启就永远读不到刚写进去的那份。

验证：

```bash
browse browsingContext getTree --table
```

出标签页列表就是通了。退出码 3（`lg:browser not connected`）就是没通，按下面「装不上
时」排查。

### 这个脚本到底动了哪些文件

装的时候写两样东西，**不止 manifest**：

1. **一个 wrapper 脚本**：`~/.local/state/lazygophers/scripts/browse-native-host`
   （Windows 是 `%LOCALAPPDATA%\lazygophers\browse\browse-native-host.cmd`），权限
   0755。内容就一行 `exec "<browse 的绝对路径>" --native-host "$@"`。
   为什么要它：native messaging 的 manifest **没有 `args` 字段**，而 native host
   就是 `browse --native-host`，那个参数没地方传，只能让 manifest 的 `path` 指向一个
   自带该参数的脚本。
2. **每个探测到的浏览器一份 manifest**：文件名恒为 `com.lazygophers.browse.json`，
   `path` 指向上面那个 wrapper。macOS 上的落点：

   | 浏览器 | 落点 |
   |---|---|
   | Chrome | `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/` |
   | Chromium | `~/Library/Application Support/Chromium/NativeMessagingHosts/` |
   | Edge | `~/Library/Application Support/Microsoft Edge/NativeMessagingHosts/` |
   | Brave | `~/Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts/` **和** Chrome 那个目录（两处来源冲突，两个都写） |
   | Opera | Chrome 那个目录 |
   | Vivaldi | `~/Library/Application Support/Vivaldi/NativeMessagingHosts/` |
   | Firefox | `~/Library/Application Support/Mozilla/NativeMessagingHosts/` |

   Linux 换成 `~/.config/<浏览器>/NativeMessagingHosts/`；Windows 不写目录写
   **HKCU 注册表键**（`SOFTWARE\<厂商>\<浏览器>\NativeMessagingHosts\com.lazygophers.browse`），
   manifest 文件统一放 `%LOCALAPPDATA%\lazygophers\browse\`。

`--uninstall` 把上面**两样都删**：所有已知落点的 manifest（不管当初探测到没有）、
Windows 注册表键、以及那个 wrapper 脚本；目录空了连目录一起删。它不碰浏览器里已加载
的扩展——那个要自己去 `chrome://extensions` 移除。

### 浏览器探测：装了但从没启动过，探测不到

脚本判断「这台机器有没有这个浏览器」看的是**用户数据目录存不存在**，而那个目录是浏览器
**第一次启动时**才创建的。所以：装了 Edge 但一次都没打开过 → 探测不到 → 不给它写
manifest。反过来也成立：卸载后残留的数据目录仍会被当成「装了」。

两种解法，先试第一种：

1. 把那个浏览器打开一次再跑脚本；
2. 跳过探测，直接点名：

```bash
browse install --browsers chrome,brave
# 可选值：chrome chromium edge brave opera vivaldi firefox（逗号分隔）
```

### 更新：Win / Mac 上**不能**自动更新

这不是没做，是做不了。在 Windows / macOS 的官方版 Chrome 上，带自分发 `update_url`
的 `.crx` 装完**立刻被禁用**（`InstallVerifier::MustRemainDisabled`，原因码
`DISABLE_NOT_VERIFIED`）；而判定「是不是商店来的」看的就是 `update_url` 指不指向
商店——**为自动更新而写的那个自分发地址，本身就是被禁用的理由**。自动更新与保持启用
在 Win/Mac 上互斥。Linux 与 Chromium 构建不受此限。

所以更新走**本地程序**：`browse` 直接改写扩展目录里的文件，再让扩展自己调
`chrome.runtime.reload()`。对已解压加载的扩展，reload 节流很宽松（要连续 30 次间隔
不到 1 秒才会被禁）。人工等价操作：重新 `npm run build`，然后在 `chrome://extensions`
上点那张卡片的「重新加载」。

**Firefox 是另一条路**：AMO unlisted 签名 + 自托管 XPI，`update_url` 生效，每天自动
检查一次。Firefox 侧的扩展 ID 是：

```
browse@lazygophers.com
```

它写在两处且必须一致——扩展的 `manifest.json` 的
`browser_specific_settings.gecko.id`，和安装脚本的 `GECKO_IDS`。有测试锁着这两处同步，
改一处不改另一处会测试失败。

### 装不上时按这个顺序查

1. `browse daemon status` —— daemon 没在跑就 `browse daemon start`
2. `chrome://extensions` 上扩展 ID 是不是 `podeceeeafjdcemppcgjhhokcokpcama`
3. `browse install --list` —— 你的浏览器在列表里吗
4. manifest 的 `path` 指的那个 wrapper 还在吗、有执行位吗
   （`ls -l ~/.local/state/lazygophers/scripts/browse-native-host`）
5. 重启浏览器（第 4 步之后没重启是最常见的原因）
6. 扩展的 service worker 控制台有没有 `[browse] daemon unavailable` 在刷

## 构建与开发

```bash
cd browser-extension/browse
npm install
npm run build      # 产出 dist/，这就是可加载的扩展目录
npm run typecheck  # tsc --noEmit，严格模式
npm test           # node --test，无第三方测试框架
```

daemon 没起时这是**正常现象**：service worker 控制台会持续打印
`[browse] daemon unavailable (...); retry #N in Nms`，间隔从 500ms 逐步退避到 60s，
连续失败 10 次后转 5 分钟长冷却。图标徽标为空；连上后显示绿点，执行指令时显示在途条数。

## 为什么用 esbuild 而不是 Vite

只打包一个 service worker（将来加一个注入脚本），没有 UI 框架、没有 HMR 需求、
没有 CSS/资源流水线。esbuild 一条 `build.mjs` 就够，Vite 的插件体系在这里全是净成本。
（对应 spec 第 10 节遗留 3，由 T05 定死。）

## 扩展 ID 与签名密钥（重要）

`manifest.json` 的 `key` 字段**写死了公钥**，因此扩展 ID 固定为：

```
podeceeeafjdcemppcgjhhokcokpcama
```

native host manifest 的 `allowed_origins` 必须写
`chrome-extension://podeceeeafjdcemppcgjhhokcokpcama/`。

不写 `key` 的话，ID 会由扩展目录的**绝对路径**算出（`extensions/common/extension.cc:176-183`），
仓库换个路径 ID 就变，`allowed_origins` 当场失配、连不上。

私钥在 `extension/keys/extension.pem`，**已被 `.gitignore` 排除，不进仓库**。

**私钥丢了无法找回**：ID 是公钥 SHA-256 前 16 字节映射到 `a`–`p`
（`components/crx_file/id_util.cc:44-57`），换一对密钥必然换 ID，没有任何缓解手段。
真丢了就得重新生成，并同步改本 README 的 ID、`manifest.json` 的 `key`、
以及所有已安装机器上的 native host manifest：

```bash
openssl genrsa -out extension/keys/extension.pem 2048
openssl rsa -in extension/keys/extension.pem -pubout -outform DER | base64 | tr -d '\n'
```

## 权限

`manifest.json` 的权限清单严格等于 spec 第 4.3 节，一条不多一条不少。
特别地**没有** `debugger`（spec 5.3 明确不做）和 `userScripts`（spec 4.3：不进 v1）。
新增权限前先改 spec。

## 目录

```
extension/
  build.mjs            esbuild 打包 + 拷贝 manifest
  src/manifest.json    MV3 manifest（含写死的 key）
  src/background.ts    入口：建连接 + 画徽标
  src/native-port.ts   connectNative、退避重连、指令分发
  src/backoff.ts       退避算法（纯函数，有单测）
  src/protocol.ts      信封类型与错误码
  src/locator.ts       元素定位（四种 scheme + 自动等待），整段跑在页面里
  src/page-locate.ts   locator 的注入入口，单独打成 iife 供 executeScript files 用
  src/events.ts        handler → daemon 的事件出口（network.* 用）
  src/handlers/        指令表，一个模块一个文件
  src/panel.{html,ts}  工具栏面板：实时日志、一键断开、免确认域名清单
  src/confirm-ui.ts    高危动作的确认弹窗（策略在 Python 侧，这里只负责问）
  test/                node --test
```

## 已实现的指令（spec 5.1 全量）

```
browsingContext.{ getTree, create, close, activate, navigate, reload, captureScreenshot }
script.{ evaluate, callFunction }
input.{ click, type, scroll, key }
storage.{ getCookies, setCookie, deleteCookies, getLocalStorage, setLocalStorage }
network.{ subscribe, unsubscribe }
lg:history.{ search, delete }
lg:bookmarks.{ search, create, remove }
lg:downloads.{ start, list, cancel }
lg:page.snapshot                    # spec 6.4 的配套发现命令
```

`lg:page.snapshot` 列出这一页所有能点、能填的元素，每条附 `css` 与 `xpath` 两种可用
locator（`browse page snapshot --table`）。它直接读 DOM，不走 locator，所以没有自动
等待可关 —— 拿到的就是此刻的页面。`--limit` 默认 200，截断时返回值里 `truncated: true`。

目标 context 选择（spec 6.5）对所有指令统一：`context` > `matchUrl` glob（匹配到
多个直接报错并列出候选）> 当前活动标签页。实现在 `handlers/context.ts`。

### 三条能力限制，写在返回值里，不靠文档提醒

1. **`input.*` 派发的是合成事件，`event.isTrusted` 恒为 `false`**。每条 `input.*`
   的返回值都带 `"lg:isTrusted": false`。校验 `isTrusted` 的站点会拒绝，绕不过去
   （spec 5.4 #1）。
2. **`browsingContext.captureScreenshot` 只截可视区**，返回值带
   `"lg:viewportOnly": true`。传 `origin: "document"` 直接回 `unsupported
   operation`，不会偷偷给一张可视区图充数（spec 5.4 #2）。
3. **`network.subscribe` 只读元数据**：URL、方法、资源类型、状态码、耗时、请求头、
   响应头。**没有 body**，MV3 的 `webRequest` 根本拿不到。事件带
   `"lg:metadataOnly": true`（spec 5.4 #3）。

### 跨浏览器

统一接口 + 显式能力协商，不做隐式降级（spec 5.5）。`chrome.*` 命名空间缺失时，
`handlers/context.ts` 的 `requireApi()` 回 `unsupported operation` 并写明缺的是哪个
API，**不会换一套实现凑合**。

### 能力协商：实现成运行时报错，不是握手时协商（有意偏离 spec 5.5）

spec 5.5 原文说的是**握手时**协商能力清单。实现改成了**运行时**拒绝：
`requireApi()` 在真要用某个 `chrome.*` 命名空间时才检查，缺了就回 `unsupported
operation` 并写明缺的是哪个 API。

结果等价 —— 两种做法都不会发生隐式降级，调用方拿到的都是显式拒绝 —— 而运行时这条不
需要在两端维护一份能力清单并保持同步。差别只在报错的时机：握手时协商能提前一步告诉你
「这个浏览器没有 downloads」，运行时这条要等你真去下载才说。

## 加一条指令

指令表在 `src/handlers/index.ts`：

- 加一条指令 = 在 `src/handlers/<module>.ts` 写一个 `Handler`，再在 `HANDLERS` 里加一行。
- 返回值即 Success 信封的 `result`。
- 要指定错误码就 `throw new CommandError(code, message)`；抛别的一律变成 `unknown error`。
- 表里查不到的 `method` 自动回 `unsupported operation`。
- context id 约定：顶层标签页是 `"<tabId>"`，子 frame 是 `"<tabId>.<frameId>"`。
- 要在页面里跑代码就用 `handlers/inject.ts` 的 `runInPage()`。**页面侧函数不能引用
  模块作用域的任何东西**——`executeScript({ func })` 只序列化函数源码，闭包全丢。
  locator 从 `globalThis.__browseLocate` 取（`page-locate.js` 注入的）。

## 给 T08（安全层）的对接点

高危动作（读写 cookie、读写 localStorage、MAIN world 执行 JS、下载、读写历史书签）
统一走 `src/handlers/confirm.ts` 的钩子，扩展侧只有调用点，**策略全在 Python 侧**。

「MAIN world 执行 JS」包括 `input.*` 用 `js=` 前缀定位的情况：它和 `script.evaluate`
一样在页面上下文跑任意 JS，只是包了一层 locator 的外衣，所以用同一个
`action: "evalMainWorld"`。另外三种前缀（`css=` / `text=` / `xpath=`）跑在 ISOLATED
world，不是高危，不走确认。

```ts
type RiskyAction =
  | "readCookies" | "writeCookies"
  | "readLocalStorage" | "writeLocalStorage"
  | "evalMainWorld" | "download"
  | "readHistory" | "writeHistory"
  | "readBookmarks" | "writeBookmarks";

interface ConfirmRequest { action: RiskyAction; method: string; url: string | null }
type ConfirmHook = (request: ConfirmRequest) => Promise<boolean>;

export function setConfirmHook(fn: ConfirmHook): void;
```

T08 要做的就是在 `background.ts` 里调 `setConfirmHook`，实现里把 `ConfirmRequest`
发给 daemon（`confirm_mode`、按域名免确认清单、审计日志都在那边），拿回 true/false。
现在的默认钩子一律放行 —— 这正是 spec 4.4 的默认值 `confirm_mode: silent`，不是漏网。

钩子返回 false 时抛的错误码是 **`lg:user rejected`**（对应 spec 6.7 的退出码 4）。
它带冒号，属于 BiDi §3.3 的扩展命名空间，`lib/browse_protocol.py` 的校验规则
「8 个标准码 + 任何含冒号的扩展码」已放行。

反方向（daemon → 扩展）一共两条控制消息，都不是能力，CLI 上敲不出来：

| method | 干什么 |
|---|---|
| `lg:confirm.request` | 把确认问题摆到用户面前，回 `{approved}`（spec 4.4） |
| `lg:context.url` | 回答「这条指令会落在哪个页面上」（spec 4.3 的 `deny_domains` 要用） |

`lg:context.url` 存在的原因：`input.*` / `script.*` 的 params 里根本没有 url，目标页是
扩展按 `context` > `matchUrl` > 活动标签页算出来的。daemon 想用拒绝名单拦住它们，就得
先问一句。问不到（没连浏览器、超时、答 null）时**按拒绝处理**——不知道打在谁身上就
不能打。名单是空的时候不问，省掉这个往返。
