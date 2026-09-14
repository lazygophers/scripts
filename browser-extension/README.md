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
  src/handlers/        指令表，一个 BiDi 模块一个文件
  test/                node --test
```

## 给 T06 / T07 的接口约定

指令表在 `src/handlers/index.ts`：

```ts
export type Handler = (params: Record<string, unknown>) => Promise<unknown>;
export const HANDLERS: Record<string, Handler> = {
  "browsingContext.getTree": browsingContextGetTree,
  "script.evaluate": scriptEvaluate,
};
```

- 加一条指令 = 在 `src/handlers/<module>.ts` 写一个 `Handler`，再在 `HANDLERS` 里加一行。
  别的地方都不用动。
- 返回值即 Success 信封的 `result`。
- 要指定错误码就 `throw new CommandError(code, message)`；抛别的一律变成 `unknown error`。
  码取值见 `src/protocol.ts`，是 BiDi 标准码。
- 表里查不到的 `method` 自动回 `unsupported operation`。
- context id 约定：顶层标签页是 `"<tabId>"`，子 frame 是 `"<tabId>.<frameId>"`。
  `handlers/script.ts` 导出 `parseContext()` 做解析，T07 的 locator 直接复用。
