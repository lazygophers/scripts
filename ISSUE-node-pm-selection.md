# Issue: build 闸对多 lockfile 的 pnpm 项目误选 yarn

## 现象
`push_canary` 在 `node/manager`（pnpm 项目）执行 build 闸时，跑的是 `yarn run build` 而非 `pnpm run build`。
在有 corepack 的环境下 `yarn` 触发下载 `yarn@1.22.22`（registry.yarnpkg.com）；离线/网络抖动即 `ECONNRESET` 失败 → build 闸 `status=fail` → `push_canary` 在**推 canary 前中止** → canary 站点永不更新（表现为「代码改了但线上还是旧的」）。

## 根因
`lib/build.py::_detect_node_pkg_manager`（约 254-267 行）按 lockfile **固定优先级** 选包管理器：
```
bun.lock > yarn.lock > pnpm-lock.yaml > package-lock.json
```
`node/manager` 与 `node/h5` 同时存在 `yarn.lock` + `pnpm-lock.yaml` + `package-lock.json`（历史遗留），yarn.lock 优先级最高 → 误选 yarn。但项目实际用 pnpm（见 workspace CLAUDE.md：`manager / h5 — pnpm`）。

## 影响面
- `node/manager`、`node/h5`：pnpm 项目但有 stale yarn.lock → 每次 push_canary/push_test 的 build 闸都可能被 yarn+corepack 卡住。
- `node/agent`、`node/merchant`：yarn 项目，选 yarn 正确，不受影响。

## 修复方向（二选一，脚本侧）
1. **优先 packageManager 字段**：若 `package.json` 有 `"packageManager": "pnpm@x"` 以它为准，再回落 lockfile 优先级。（manager 当前无此字段，可要求前端补上。）
2. **lockfile 冲突时的 tie-break**：多 lockfile 并存时，改为按 mtime 最新，或按 `node_modules/.modules.yaml`（pnpm 特征文件）/ `node_modules/.package-lock.json` 探测实际安装的 pm，而非纯静态优先级。

## 仓库侧配套（可选，治本）
`node/manager`、`node/h5` 删掉 stale `yarn.lock` + `package-lock.json`，只留 `pnpm-lock.yaml`，并在 package.json 加 `"packageManager": "pnpm@<version>"`。

## 已验证
- feature/nico/channel-report-timezone 分支 manager 代码 **`vue-cli-service build` 直跑 exit 0**（编译无误），说明 build 闸失败纯粹是 pm 选择 + corepack 联网问题，非代码问题。
