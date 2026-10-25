# 浏览器扩展（MV3）陷阱

- **MV3 service worker 随时被终止，跨事件的队列/状态必须落 `chrome.storage`**：
  内存里的 pending 队列在 SW 休眠时连内容带 flush 时机一起丢（2026-10-25 扩展
  遥测 5690b03 的设计被对抗评审按 MV3 官方文档否掉重来）。凡是要活过下一个事件
  的状态，写 storage、事件触发时读回，flush 时机跟着 storage 走不跟着内存走。
- **大改扩展代码前先查 developer.chrome.com 对应 API 页**：2026-10-25 对抗评审
  两次靠官方文档否掉自查通过的设计（内存 pending 队列违反 SW 生命周期）。设计稿
  和记忆都不算出处，webgrab 一手文档再定设计。
