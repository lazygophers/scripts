/**
 * 设置页，兼装好扩展之后的欢迎页（地址带 `?welcome=1` 时多出上手说明那一段）。
 *
 * 只有两件事要交代给用户：
 *
 * 1. 「允许访问文件网址」这个开关必须用户自己去浏览器里打开，扩展代劳不了；
 *    但扩展读得到它的真实状态（`chrome.extension.isAllowedFileSchemeAccess()`，
 *    <https://developer.chrome.com/docs/extensions/reference/api/extension#method-isAllowedFileSchemeAccess>），
 *    所以页面上写的是实况，不是「大概没开吧」。
 * 2. 有几类文件浏览器会直接下载，viewer 根本没机会出手。默认不管这件事，
 *    用户自己打开「强制拦截」才拦，拦法见 `rules.ts`。
 */

import { applyRules } from "./rules.ts";

/** 设置存在扩展自己的存储里，浏览器重启照样在。 */
const KEY = "viewer-settings";

export type Settings = {
  /** 会被浏览器下载的本地文本文件，改为在扩展自己的展示页里打开。默认关。 */
  force: boolean;
};

const DEFAULTS: Settings = { force: false };

export async function readSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get(KEY);
  return { ...DEFAULTS, ...((stored[KEY] as Partial<Settings> | undefined) ?? {}) };
}

export async function writeSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await readSettings()), ...patch };
  await chrome.storage.local.set({ [KEY]: next });
  // 拦截规则跟着开关走，改完立刻生效，不用重装扩展。
  if (patch.force !== undefined) await applyRules(next.force);
  return next;
}

/** 浏览器里那个「允许访问文件网址」开关现在是开是关。 */
export async function fileAccess(): Promise<boolean> {
  // Firefox 没有这个方法：那边扩展本来就读得到本地文件，等于一直是开着的。
  if (typeof chrome.extension?.isAllowedFileSchemeAccess !== "function") return true;
  return chrome.extension.isAllowedFileSchemeAccess();
}

/**
 * 把页面接上：权限状态写成实况，开关读存储、改存储。
 *
 * 页面骨架写在 `settings.html` 里，这里只填内容和挂事件——这样测试给一份同样 id 的
 * 骨架就能跑，不必把一堆 `createElement` 塞进来。
 */
export async function renderSettings(doc: Document): Promise<void> {
  const allowed = await fileAccess();
  const status = doc.getElementById("lfv-access") as HTMLElement;
  status.textContent = allowed ? "已经打开，本地文件可以正常显示" : "还没打开，本地文件显示不了";
  status.dataset["state"] = allowed ? "on" : "off";

  // 已经打开的用户不必再看那一串步骤，只有真的没打开时才摊开。
  // Firefox 没有这个开关，也就没有 `isAllowedFileSchemeAccess`，那边照着 Chrome 的步骤
  // 走只会扑空，所以换一句话说明。
  const chromium = typeof chrome.extension?.isAllowedFileSchemeAccess === "function";
  (doc.getElementById("lfv-steps") as HTMLElement).hidden = allowed || !chromium;
  (doc.getElementById("lfv-firefox") as HTMLElement).hidden = chromium;

  // 装好之后自动弹出来的那一次多一句欢迎话；从菜单里点进来的就只是设置页。
  const hello = doc.getElementById("lfv-welcome") as HTMLElement;
  hello.hidden = !new URL(doc.URL).searchParams.has("welcome");

  const force = doc.getElementById("lfv-force") as HTMLInputElement;
  force.checked = (await readSettings()).force;
  force.addEventListener("change", () => {
    void writeSettings({ force: force.checked });
  });
}
