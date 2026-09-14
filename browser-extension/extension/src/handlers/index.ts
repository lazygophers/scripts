import { record } from "../audit.ts";
import { domainOf, enforceDenyList, enforceFeatureToggles, riskyAction, targetUrl } from "../policy.ts";
import { CommandError } from "../protocol.ts";
import { auditClear, auditRead } from "./audit.ts";
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
import { downloadsCancel, downloadsList, downloadsStart } from "./downloads.ts";
import { historyDelete, historySearch } from "./history.ts";
import { inputClick, inputKey, inputScroll, inputType } from "./input.ts";
import { networkSubscribe, networkUnsubscribe } from "./network.ts";
import { pageSnapshot } from "./page.ts";
import { scriptCallFunction, scriptEvaluate } from "./script.ts";
import {
  storageDeleteCookies,
  storageGetCookies,
  storageGetLocalStorage,
  storageSetCookie,
  storageSetLocalStorage,
} from "./storage.ts";

export type Handler = (params: Record<string, unknown>) => Promise<unknown>;

/**
 * The command table. Key is the wire `method`, value returns the `result`
 * payload of a Success reply. Throw `CommandError` to pick an error code;
 * anything else becomes `unknown error`.
 *
 * This is the whole of spec 5.1 (v1). Anything outside it — a v2 capability, a
 * CDP-only one — is absent on purpose and `dispatch` answers `unsupported
 * operation`. There is no per-browser variant of this table: spec 5.5 forbids
 * swapping in a different implementation on Firefox, so a missing `chrome.*`
 * namespace surfaces as an explicit refusal from `requireApi` instead.
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
};

/**
 * 读审计本身不记审计。不排除的话 `browse audit` 每跑一次都会给日志添一条「我读了
 * 日志」，越读越长。
 */
const NOT_AUDITED = new Set(["lg:audit.read", "lg:audit.clear"]);

/**
 * 一条指令的完整一生：拒绝名单 → 执行 → 记账。
 *
 * 2026-09-14 之前这三件事在 daemon 里（`_gate` + `_audit`）。搬过来之后这里是唯一的
 * 收口 —— 每条指令都从这儿过，漏不掉。
 *
 * 拒绝名单管**全部**方法，不只高危的那些：拉黑一个域名之后连导航过去都不该允许，所以
 * 它在 handler 之前、`confirm()` 之外单独走一道。
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
  const domain = domainOf(targetUrl(params));
  try {
    await enforceDenyList(method, params);
    await enforceFeatureToggles(method, params);
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
}
