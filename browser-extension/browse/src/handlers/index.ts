import { record } from "../audit.ts";
import { domainOf, enforceDenyList, enforceFeatureToggles, riskyAction, targetUrl } from "../policy.ts";
import { CommandError } from "../protocol.ts";
import { auditClear, auditRead } from "./audit.ts";
import { dropContextCache, resolveContextOnce, targetUrl as tabUrl } from "./context.ts";
import { bookmarksCreate, bookmarksRemove, bookmarksSearch } from "./bookmarks.ts";
import {
  browsingContextActivate,
  browsingContextCaptureScreenshot,
  browsingContextClose,
  browsingContextCreate,
  browsingContextGetTree,
  browsingContextNavigate,
  browsingContextReload,
} from "./browsingContext.ts";
import {
  captureRecordDesktop,
  captureRecordStop,
  captureRecordTab,
  offscreenDocuments,
  pageCaptureSaveMhtml,
} from "./capture.ts";
import { clipboardRead, clipboardWrite } from "./clipboard.ts";
import {
  downloadsCancel,
  downloadsList,
  downloadsOpen,
  downloadsStart,
} from "./downloads.ts";
import { gcmDeleteToken, gcmId, gcmToken } from "./gcm.ts";
import { historyDelete, historySearch } from "./history.ts";
import {
  dnsResolve,
  idleState,
  processesList,
  searchQuery,
  systemInfo,
  topSitesList,
} from "./info.ts";
import { inputClick, inputKey, inputScroll, inputType } from "./input.ts";
import { networkSubscribe, networkUnsubscribe } from "./network.ts";
import {
  notificationsClear,
  notificationsShow,
  powerKeepAwake,
  powerRelease,
} from "./notify.ts";
import { pageSnapshot } from "./page.ts";
import {
  permissionsContains,
  permissionsGetAll,
} from "./perms.ts";
import {
  printingRespond,
} from "./printing.ts";
import { proxyClear, proxyGet, proxySet } from "./proxy.ts";
import {
  readingListAdd,
  readingListList,
  readingListRemove,
  readingListUpdate,
} from "./readingList.ts";
import { scriptCallFunction, scriptEvaluate } from "./script.ts";
import {
  declContentClear,
  declContentSetRules,
  userScriptsList,
  userScriptsRegister,
  userScriptsReset,
  userScriptsUnregister,
  userScriptsWorld,
} from "./scripts.ts";
import { tabsGroup, tabsGroups, tabsUngroup, tabsUpdateGroup } from "./tabs.ts";
import {
  storageDeleteCookies,
  storageGetCookies,
  storageGetLocalStorage,
  storageSetCookie,
  storageSetLocalStorage,
} from "./storage.ts";
import {
  commandsList,
  omniboxSetDefault,
  sidePanelBehavior,
  sidePanelClose,
  sidePanelOpen,
} from "./ui.ts";
import { wauthAttach, wauthComplete, wauthDetach } from "./wauth.ts";

export type Handler = (params: Record<string, unknown>) => Promise<unknown>;

/**
 * The command table. Key is the wire `method`, value returns the `result`
 * payload of a Success reply. Throw `CommandError` to pick an error code;
 * anything else becomes `unknown error`.
 *
 * Anything outside the table is absent on purpose and `dispatch` answers
 * `unsupported operation`. There is no per-browser variant of this table:
 * spec 5.5 forbids swapping in a different implementation on Firefox, so a
 * missing `chrome.*` namespace surfaces as an explicit refusal from
 * `requireApi` instead.
 */
export const HANDLERS: Record<string, Handler> = {
  "browsingContext.getTree": browsingContextGetTree,
  "browsingContext.create": browsingContextCreate,
  "browsingContext.close": browsingContextClose,
  "browsingContext.activate": browsingContextActivate,
  "browsingContext.navigate": browsingContextNavigate,
  "browsingContext.reload": browsingContextReload,
  "browsingContext.captureScreenshot": browsingContextCaptureScreenshot,

  "script.evaluate": scriptEvaluate,
  "script.callFunction": scriptCallFunction,

  "input.click": inputClick,
  "input.type": inputType,
  "input.scroll": inputScroll,
  "input.key": inputKey,

  "storage.getCookies": storageGetCookies,
  "storage.setCookie": storageSetCookie,
  "storage.deleteCookies": storageDeleteCookies,
  "storage.getLocalStorage": storageGetLocalStorage,
  "storage.setLocalStorage": storageSetLocalStorage,

  "network.subscribe": networkSubscribe,
  "network.unsubscribe": networkUnsubscribe,

  "lg:history.search": historySearch,
  "lg:history.delete": historyDelete,

  "lg:bookmarks.search": bookmarksSearch,
  "lg:bookmarks.create": bookmarksCreate,
  "lg:bookmarks.remove": bookmarksRemove,

  "lg:page.snapshot": pageSnapshot,

  // 审计的出口（spec 4.6）。日志存在 chrome.storage.local 里，命令行读不到那个存储，
  // 这两条就是 `browse audit` 唯一的取数路径。
  "lg:audit.read": auditRead,
  "lg:audit.clear": auditClear,

  "lg:downloads.start": downloadsStart,
  "lg:downloads.list": downloadsList,
  "lg:downloads.cancel": downloadsCancel,
  "lg:downloads.open": downloadsOpen,

  "lg:tabs.group": tabsGroup,
  "lg:tabs.ungroup": tabsUngroup,
  "lg:tabs.groups": tabsGroups,
  "lg:tabs.updateGroup": tabsUpdateGroup,

  // 2026-09-16 扩容的能力面，全部 `lg:` 私有方法（BiDi §3.3 的冒号保留）。
  "lg:pageCapture.saveMhtml": pageCaptureSaveMhtml,
  "lg:capture.recordTab": captureRecordTab,
  "lg:capture.recordStop": captureRecordStop,
  "lg:capture.recordDesktop": captureRecordDesktop,
  "lg:offscreen.documents": offscreenDocuments,

  "lg:clipboard.read": clipboardRead,
  "lg:clipboard.write": clipboardWrite,

  "lg:readingList.list": readingListList,
  "lg:readingList.add": readingListAdd,
  "lg:readingList.update": readingListUpdate,
  "lg:readingList.remove": readingListRemove,

  "lg:topSites.list": topSitesList,
  "lg:search.query": searchQuery,
  "lg:dns.resolve": dnsResolve,
  "lg:idle.state": idleState,
  "lg:processes.list": processesList,
  "lg:system.info": systemInfo,

  "lg:notifications.show": notificationsShow,
  "lg:notifications.clear": notificationsClear,
  "lg:power.keepAwake": powerKeepAwake,
  "lg:power.release": powerRelease,

  "lg:proxy.get": proxyGet,
  "lg:proxy.set": proxySet,
  "lg:proxy.clear": proxyClear,

  "lg:permissions.getAll": permissionsGetAll,
  "lg:permissions.contains": permissionsContains,

  "lg:gcm.id": gcmId,
  "lg:gcm.token": gcmToken,
  "lg:gcm.deleteToken": gcmDeleteToken,

  "lg:userScripts.register": userScriptsRegister,
  "lg:userScripts.list": userScriptsList,
  "lg:userScripts.unregister": userScriptsUnregister,
  "lg:userScripts.reset": userScriptsReset,
  "lg:userScripts.world": userScriptsWorld,
  "lg:declContent.setRules": declContentSetRules,
  "lg:declContent.clear": declContentClear,

  "lg:commands.list": commandsList,
  "lg:sidePanel.open": sidePanelOpen,
  "lg:sidePanel.close": sidePanelClose,
  "lg:sidePanel.behavior": sidePanelBehavior,
  "lg:omnibox.setDefault": omniboxSetDefault,

  "lg:wauth.attach": wauthAttach,
  "lg:wauth.detach": wauthDetach,
  "lg:wauth.complete": wauthComplete,

  "lg:printing.respond": printingRespond,
};

/**
 * 读审计本身不记审计。不排除的话 `browse audit` 每跑一次都会给日志添一条「我读了
 * 日志」，越读越长。
 */
const NOT_AUDITED = new Set(["lg:audit.read", "lg:audit.clear"]);

/**
 * 作用对象是一个页面 context 的方法：域名策略（deny / feature）要跟着解析出来的
 * 真实页面走，不只看 params 里有没有 `url` / `domain`。表外的全局方法（列书签、
 * 搜历史、设代理）没有目标域，策略里的域名维度对它们不适用 —— 全局禁用照管。
 *
 * 与 `resolveContextOnce` 的调用方对齐：handler 会解析 context 的方法都在这里。
 */
const PAGE_METHODS = new Set([
  "browsingContext.close",
  "browsingContext.activate",
  "browsingContext.navigate",
  "browsingContext.reload",
  "browsingContext.captureScreenshot",
  "script.evaluate",
  "script.callFunction",
  "input.click",
  "input.type",
  "input.scroll",
  "input.key",
  "storage.getLocalStorage",
  "storage.setLocalStorage",
  "lg:page.snapshot",
  "lg:tabs.group",
  "lg:tabs.ungroup",
  "lg:pageCapture.saveMhtml",
  "lg:capture.recordTab",
]);

/** 这条指令的域名策略要不要跟解析出的页面目标走：页面方法，或显式给了 context / matchUrl。 */
function wantsContext(method: string, params: Record<string, unknown>): boolean {
  return PAGE_METHODS.has(method)
    || params.context !== undefined
    || params.matchUrl !== undefined;
}

/**
 * 策略目标（CONTEXT.md）：这条指令真正作用到的页面地址。显式 `url` / `domain`
 * 直接用；页面方法解析 context 拿真实 tab URL —— deny / feature / 确认 / 审计
 * 共用这一份。读不到（标签页没了）就 fail closed 拒绝，绝不带着 null 放行。
 */
async function resolvePolicyTarget(
  method: string,
  params: Record<string, unknown>,
): Promise<string | null> {
  const explicit = targetUrl(params);
  if (explicit) {
    return explicit;
  }
  if (!wantsContext(method, params)) {
    return null; // 全局动作：没有目标域，只有全局禁用管得到
  }
  const target = await resolveContextOnce(params); // 解析失败本身就是拒绝（fail closed）
  const url = await tabUrl(target);
  if (url === null) {
    throw new CommandError(
      "no such frame",
      `读不到目标页面的 URL，${method} 拒绝执行（fail closed）`,
    );
  }
  return url;
}

/**
 * 一条指令的完整一生：解析策略目标 → 拒绝名单 → 功能开关 → 执行 → 记账。
 *
 * 2026-09-14 之前这三件事在 daemon 里（`_gate` + `_audit`）。搬过来之后这里是唯一的
 * 收口 —— 每条指令都从这儿过，漏不掉。
 *
 * 拒绝名单管**全部**方法，不只高危的那些：拉黑一个域名之后连导航过去都不该允许，所以
 * 它在 handler 之前、`confirm()` 之外单独走一道。域名取自策略目标：隐式 context
 * （context / matchUrl / 当前标签页）解析出的真实页面 URL 也算数 —— 2026-09-21 之前
 * 只看 params.url/domain，页面动作可以绕过域名规则。
 */
export async function dispatch(
  method: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  const handler = HANDLERS[method];
  if (!handler) {
    throw new CommandError(
      method.includes(".") ? "unsupported operation" : "unknown command",
      `no handler for ${method}`,
    );
  }
  if (NOT_AUDITED.has(method)) {
    return handler(params);
  }

  const started = Date.now();
  let domain: string | null = null;
  try {
    try {
      const url = await resolvePolicyTarget(method, params);
      domain = domainOf(url);
      await enforceDenyList(method, url);
      await enforceFeatureToggles(method, url);
    } catch (err) {
      await record({
        ts: new Date().toISOString(),
        method,
        domain,
        action: riskyAction(method, params),
        result: "denied",
        ms: Date.now() - started,
        error: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }

    try {
      const result = await handler(params);
      await record({
        ts: new Date().toISOString(),
        method,
        domain,
        action: riskyAction(method, params),
        result: "success",
        ms: Date.now() - started,
      });
      return result;
    } catch (err) {
      const rejected = err instanceof CommandError && err.code === "lg:user rejected";
      await record({
        ts: new Date().toISOString(),
        method,
        domain,
        action: riskyAction(method, params),
        result: rejected ? "denied" : "error",
        ms: Date.now() - started,
        error: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  } finally {
    dropContextCache(params);
  }
}
