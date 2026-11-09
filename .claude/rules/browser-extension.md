# 浏览器扩展（MV3）陷阱

- **MV3 service worker 随时被终止，跨事件的队列/状态必须落 `chrome.storage`**：
  内存里的 pending 队列在 SW 休眠时连内容带 flush 时机一起丢（2026-10-25 扩展
  遥测 5690b03 的设计被对抗评审按 MV3 官方文档否掉重来）。凡是要活过下一个事件
  的状态，写 storage、事件触发时读回，flush 时机跟着 storage 走不跟着内存走。
- **大改扩展代码前先查 developer.chrome.com 对应 API 页**：2026-10-25 对抗评审
  两次靠官方文档否掉自查通过的设计（内存 pending 队列违反 SW 生命周期）。设计稿
  和记忆都不算出处，webgrab 一手文档再定设计。
- **三包依赖钉精确版本，不用 `^`**（2026-11-08 commit efbc589）：`^` 让
  lodash-es 飘进 4.18.0——npm 已 deprecate 的坏版本（警告原文 "Bad release.
  Please use lodash-es@4.17.23 instead"），同轮还实测到 esbuild 装坏半截
  （`shared/node_modules/esbuild` 缺 package.json）。shared/browse/viewer 全部
  去 `^`，viewer 用 `overrides` 把 lodash-es 钉回 4.17.23。
- **@mermaid-js/parser 在本机 npm 解包会丢 `dist/*.mjs`**（2026-11-09）：
  tarball 完整、`--force` / `--prefer-online` 均复现、根因未明。删 node_modules
  重装 viewer 后构建报 `Could not resolve "@mermaid-js/parser"`，`npm install`
  修不回来；补法 = 下载官方 tarball 手工 `tar` 解进 node_modules。遇到扩展包
  构建报模块找不到、重装无效时先查解包目录里 dist 是否齐全，别反复重试 npm。
