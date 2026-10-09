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

/**
 * 目录悬停预览的取文件头。内容脚本 fetch `file://` 会被 CORS 拦（file:// 页面
 * origin 是 null），只有后台带着 `host_permissions` 去读才放行，所以这一步只能
 * 由这里代劳。只回第一块数据，回多少由调用方自己裁。
 */
export async function headFile(message: unknown): Promise<{ ok: boolean; text?: string; error?: string }> {
  const { type, url } = (message ?? {}) as { type?: unknown; url?: unknown };
  if (type !== "lfv-head" || typeof url !== "string" || !url.startsWith("file://")) {
    return { ok: false, error: "不是可读的 file:// 地址" };
  }
  try {
    const response = await fetch(url);
    // firstChunk 和 listing.ts 里那份各守一个 bundle，不值得为几行开共享模块。
    const reader = response.body?.getReader();
    const chunk = await reader?.read();
    void reader?.cancel();
    // 截到 64 KB：文本预览几行就够，html 渲染预览需要更多，都够用且不整文件拉。
    return { ok: true, text: (chunk?.value === undefined ? await response.text() : new TextDecoder().decode(chunk.value)).slice(0, 64 * 1024) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * 「在资源管理器打开」：交给 native host 去跑（扩展进程起不了系统程序）。
 * host 由仓库里的 `viewer-reveal install` 注册（`com.lazygophers.viewer_reveal`），
 * 没装时 Chrome 直接在 sendNativeMessage 的回调里给 lastError，原样带回去。
 */
export function revealInFileManager(
  url: string,
): Promise<{ ok: boolean; message?: string | undefined; error?: string | undefined }> {
  const path = decodeURIComponent(new URL(url).pathname);
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage("com.lazygophers.viewer_reveal", { path }, (reply: unknown) => {
        const failure = chrome.runtime.lastError?.message;
        if (failure !== undefined) return resolve({ ok: false, error: failure });
        const typed = reply as { ok?: boolean; message?: string; error?: string } | undefined;
        resolve(typed === undefined
          ? { ok: false, error: "host 没回话" }
          : {
            ok: typed.ok === true,
            ...(typed.message === undefined ? {} : { message: typed.message }),
            ...(typed.error === undefined ? {} : { error: typed.error }),
          });
      });
    } catch (error) {
      resolve({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  });
}

chrome.runtime.onMessage.addListener((message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (response: unknown) => void) => {
  if (openLocal(message, sender.tab?.id)) return false;
  const { type } = (message ?? {}) as { type?: unknown };
  if (type === "lfv-reveal") {
    const { url } = (message ?? {}) as { url?: unknown };
    if (typeof url === "string" && url.startsWith("file://")) {
      void revealInFileManager(url).then(sendResponse);
      return true;
    }
    sendResponse({ ok: false, error: "不是 file:// 地址" });
    return true;
  }
  if (type !== "lfv-head") return false;
  void headFile(message).then(sendResponse);
  return true;
});
