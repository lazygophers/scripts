/**
 * 后台脚本，只管两件和权限有关的事。
 *
 * 一、装好之后弹一次欢迎页。「允许访问文件网址」那个开关扩展代劳不了，只能教用户去开；
 * 已经开着的用户不弹，别烦人。
 *
 * 二、强制拦截。`.yaml`、`.csv` 这类文件浏览器直接下载，内容脚本根本进不去。开关打开后，
 * 下载一冒头就取消，改在扩展自己的展示页里打开同一个文件。
 *
 * 另一条路是 declarativeNetRequest 的重定向规则（本项目早先的探针扩展实测过，`file://`
 * 的主框架导航确实拦得住）。没走那条：dNR 规则要事先写死一份「哪些扩展名会被下载」的清单，
 * 而这份清单因机器而异。下载事件本身就是浏览器在说「这个我不显示」，按它来不必猜。
 */
import { classify } from "./prettify.ts";
import { readSettings } from "./settings.ts";

/** 一条下载记录里我们要用的那几个字段。 */
type Download = { id: number; url: string; finalUrl?: string };

/** 装好扩展时弹欢迎页。返回是否真的弹了，测试据此断言。 */
export async function welcome(reason: string): Promise<boolean> {
  if (reason !== "install") return false;
  if (await chrome.extension.isAllowedFileSchemeAccess()) return false;
  await chrome.tabs.create({ url: chrome.runtime.getURL("settings.html?welcome=1") });
  return true;
}

/** 拦下一条下载，改在展示页里打开。返回是否拦了。 */
export async function interceptDownload(item: Download): Promise<boolean> {
  const url = item.finalUrl ?? item.url;
  if (!url.startsWith("file://")) return false;
  if (classify(url) !== "whitelist") return false;
  if (!(await readSettings()).force) return false;

  await chrome.downloads.cancel(item.id);
  // 取消掉的那条还留在下载列表里，一并抹掉，免得每看一个文件就多一条失败记录。
  await chrome.downloads.erase({ id: item.id });
  await chrome.tabs.create({
    url: chrome.runtime.getURL(`viewer.html?file=${encodeURIComponent(url)}`),
  });
  return true;
}

chrome.runtime.onInstalled.addListener((details: { reason: string }) => {
  void welcome(details.reason);
});

chrome.downloads.onCreated.addListener((item: Download) => {
  void interceptDownload(item);
});
