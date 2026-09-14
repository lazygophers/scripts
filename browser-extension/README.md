# browse 浏览器扩展

`browse` 的浏览器端。通过 Native Messaging 连本机 daemon（host 名 `com.lazygophers.browse`），
收 WebDriver BiDi 形状的指令，执行后回结果。方案见 `.scratch/browser-control-extension/spec.md`。

## 安装（Chrome / Edge / Brave，四步）

方案第 7.1 节。**不走 Chrome Web Store，自己分发，手动更新。**

### 第 1 步：构建出可加载的扩展目录

```bash
cd browser-extension/extension
npm install
npm run build      # 产出 dist/，这就是要加载的那个目录
```

`dist/` 的绝对路径下一步要用，先 `pwd` 记下来：`<仓库>/browser-extension/extension/dist`。

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

### 第 4 步：注册 native host manifest

```bash
python3 browser-extension/install/native_host.py          # 装
python3 browser-extension/install/native_host.py --list   # 只看会写哪些文件，不动盘
python3 browser-extension/install/native_host.py --uninstall  # 卸
```

**这个脚本没有登记进 `pyproject.toml` 的 `[project.scripts]`**，所以
`uvx --from git+https://github.com/lazygophers/scripts ...` 装不到它。要注册 native
host，只能 clone 仓库后按上面的路径跑 `python3`。（`browse` 本身有入口，`uvx` 装得到；
装不到的只是这个安装脚本。）

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
python3 browser-extension/install/native_host.py --browsers chrome,brave
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
3. `python3 browser-extension/install/native_host.py --list` —— 你的浏览器在列表里吗
4. manifest 的 `path` 指的那个 wrapper 还在吗、有执行位吗
   （`ls -l ~/.local/state/lazygophers/scripts/browse-native-host`）
5. 重启浏览器（第 4 步之后没重启是最常见的原因）
6. 扩展的 service worker 控制台有没有 `[browse] daemon unavailable` 在刷

## 构建与开发

```bash
cd browser-extension/extension
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
```

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
