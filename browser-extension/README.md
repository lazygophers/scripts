# browse 浏览器扩展

`browse` 的浏览器端。通过 Native Messaging 连本机 daemon（host 名 `com.lazygophers.browse`），
收 WebDriver BiDi 形状的指令，执行后回结果。方案见 `.scratch/browser-control-extension/spec.md`。

## 构建

```bash
cd browser-extension/extension
npm install
npm run build      # 产出 dist/，这就是可加载的扩展目录
npm run typecheck  # tsc --noEmit，严格模式
npm test           # node --test，无第三方测试框架
```

加载：`chrome://extensions` → 打开「开发者模式」→「加载已解压的扩展程序」→ 选
`browser-extension/extension/dist`。

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
统一走 `src/handlers/confirm.ts` 的钩子，扩展侧只有调用点，**策略全在 Python 侧**：

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
