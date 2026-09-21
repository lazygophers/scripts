/**
 * 后台脚本，只管装好扩展之后那一次引导。
 *
 * 「允许访问文件网址」那个开关扩展代劳不了，只能教用户去开；已经开着的用户不弹，别烦人。
 *
 * 强制拦截不在这里：它是 `declarativeNetRequest` 的动态规则（`intercept.ts`），由设置页
 * 拨开关时写进浏览器，之后由浏览器自己执行，后台脚本醒不醒都一样。
 */

/** 装好扩展时弹欢迎页。返回是否真的弹了，测试据此断言。 */
export async function welcome(reason: string): Promise<boolean> {
  if (reason !== "install") return false;
  // Firefox 没有这个方法，那边也没有要教用户去开的开关，直接不弹。
  if (typeof chrome.extension?.isAllowedFileSchemeAccess !== "function") return false;
  if (await chrome.extension.isAllowedFileSchemeAccess()) return false;
  await chrome.tabs.create({ url: chrome.runtime.getURL("settings.html?welcome=1") });
  return true;
}

chrome.runtime.onInstalled.addListener((details: { reason: string }) => {
  void welcome(details.reason);
});

/**
 * 展示页里点本地文件链接时的跳转。
 *
 * Chrome 不让 `chrome-extension://` 的页面自己导航到 `file://`（点了毫无反应），
 * 但后台脚本调 `chrome.tabs.update` 可以——所以这一步只能由这里代劳。
 * 文件页上的链接不走这条路，那边浏览器自己就能跳。
 */
export function openLocal(message: unknown, tabId: number | undefined): boolean {
  const { type, url } = (message ?? {}) as { type?: unknown; url?: unknown };
  if (type !== "lfv-open" || typeof url !== "string" || tabId === undefined) return false;
  if (!url.startsWith("file://")) return false;
  void chrome.tabs.update(tabId, { url });
  return true;
}

chrome.runtime.onMessage.addListener((message: unknown, sender: chrome.runtime.MessageSender) => {
  openLocal(message, sender.tab?.id);
});
