import { CommandError } from "../protocol.ts";
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

  "lg:downloads.start": downloadsStart,
  "lg:downloads.list": downloadsList,
  "lg:downloads.cancel": downloadsCancel,
};

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
  return handler(params);
}
