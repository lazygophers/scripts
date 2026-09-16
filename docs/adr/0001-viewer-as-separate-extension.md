# viewer 是独立扩展，不并进 browse

本仓已经有一个浏览器扩展 `browse`，它是 CLI 的远端执行器：接收本机 daemon 转发来的指令、执行、回包，并持有确认策略与审计日志。本地文件展示增强（`viewer`）与它共享的只有构建工具与深色色板，没有任何运行时耦合——viewer 不需要 native messaging，browse 不需要 content script。把两者塞进一个扩展会让 browse 的权限面凭空多出 `<all_urls>` 与 `file:///*`，而 browse 的整套确认策略正是围绕「高危动作要人点头」建立的，权限膨胀直接削弱那套策略的可信度。因此 `browser-extension/` 下按扩展分目录（`browse/` / `viewer/`），公共部分下沉到 `shared/`。

## Consequences

`browse` 的代码要从 `browser-extension/extension/` 搬到 `browser-extension/browse/`，`lib/browse_install.py` 里写死的路径要跟着改。native host manifest 的 `path` 指向 `bin/browse` 而非扩展目录，所以已经装过的用户不受影响。
