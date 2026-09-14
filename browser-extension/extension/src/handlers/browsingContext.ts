import { CommandError, optionalString, requireString } from "../protocol.ts";
import { formatContext, requireApi, resolveContext, type Target } from "./context.ts";

interface ContextInfo {
  context: string;
  parent: string | null;
  url: string;
  children: ContextInfo[];
  /** Not in BiDi: the tab title, which every caller of this wants anyway. */
  "lg:title"?: string;
  "lg:active"?: boolean;
}

/**
 * BiDi `browsingContext.getTree`. Top-level contexts are tabs; children are
 * frames from `webNavigation.getAllFrames`. Context ids are `<tabId>` for a
 * tab and `<tabId>.<frameId>` for a frame, so any later command can address a
 * frame without a second lookup.
 */
export async function browsingContextGetTree(
  params: Record<string, unknown>,
): Promise<{ contexts: ContextInfo[] }> {
  const root = optionalString(params.root, "root", ", a context id");

  const tabs = await chrome.tabs.query({});
  const wanted = root === undefined ? tabs : tabs.filter((t) => String(t.id) === root);
  if (root !== undefined && wanted.length === 0) {
    throw new CommandError("no such frame", `no browsing context ${root}`);
  }

  const contexts = await Promise.all(wanted.map(tabToContext));
  return { contexts };
}

/**
 * `browsingContext.create`. `type` is `tab` (default) or `window`; `background`
 * opens without focusing. Returns the new context id.
 */
export async function browsingContextCreate(
  params: Record<string, unknown>,
): Promise<{ context: string }> {
  const url = optionalString(params.url, "url");
  const type = params.type === undefined ? "tab" : params.type;
  if (type !== "tab" && type !== "window") {
    throw new CommandError("invalid argument", `type must be "tab" or "window"`);
  }
  const background = params.background === true;

  if (type === "window") {
    requireApi("windows.create", "opening a window");
    const win = await chrome.windows.create({
      ...(url === undefined ? {} : { url }),
      focused: !background,
    });
    const tabId = win?.tabs?.[0]?.id;
    if (tabId === undefined) {
      throw new CommandError("unknown error", "window opened without a tab");
    }
    return { context: String(tabId) };
  }

  const tab = await chrome.tabs.create({
    ...(url === undefined ? {} : { url }),
    active: !background,
  });
  if (tab.id === undefined) {
    throw new CommandError("unknown error", "tab opened without an id");
  }
  return { context: String(tab.id) };
}

/** `browsingContext.close`. Closes the tab; a frame id is rejected. */
export async function browsingContextClose(
  params: Record<string, unknown>,
): Promise<Record<string, never>> {
  const target = await requireTabOnly(params, "close");
  await chrome.tabs.remove(target.tabId);
  return {};
}

/** `browsingContext.activate`. Focuses the tab and its window. */
export async function browsingContextActivate(
  params: Record<string, unknown>,
): Promise<Record<string, never>> {
  const target = await requireTabOnly(params, "activate");
  await activate(target.tabId);
  return {};
}

/**
 * `browsingContext.navigate`. Waits for the load to finish unless
 * `wait: "none"`. Frame-scoped navigation is not offered: `chrome.tabs.update`
 * only drives the top level, and faking it by setting `frame.location` from an
 * injected script is a different behaviour, which spec 5.5 forbids.
 */
export async function browsingContextNavigate(
  params: Record<string, unknown>,
): Promise<{ navigation: null; url: string }> {
  const url = requireString(params.url, "url");
  const target = await requireTabOnly(params, "navigate");
  await chrome.tabs.update(target.tabId, { url });
  if (params.wait !== "none") {
    await waitForLoad(target.tabId, timeoutOf(params));
  }
  const tab = await chrome.tabs.get(target.tabId);
  return { navigation: null, url: tab.url ?? url };
}

/** `browsingContext.reload`. `ignoreCache` bypasses the HTTP cache. */
export async function browsingContextReload(
  params: Record<string, unknown>,
): Promise<{ navigation: null; url: string }> {
  const target = await requireTabOnly(params, "reload");
  await chrome.tabs.reload(target.tabId, { bypassCache: params.ignoreCache === true });
  if (params.wait !== "none") {
    await waitForLoad(target.tabId, timeoutOf(params));
  }
  const tab = await chrome.tabs.get(target.tabId);
  return { navigation: null, url: tab.url ?? "" };
}

/**
 * `browsingContext.captureScreenshot`. **Viewport only** (spec 5.4): the API is
 * `tabs.captureVisibleTab`, which photographs what is on screen. Scroll-and-
 * stitch for a full page is explicitly out of v1, so `origin: "document"` is
 * refused rather than silently answered with a viewport shot.
 *
 * `captureVisibleTab` can only photograph the active tab of a window, so an
 * inactive target is activated first; the result says so.
 */
export async function browsingContextCaptureScreenshot(
  params: Record<string, unknown>,
): Promise<{ data: string; "lg:viewportOnly": true; "lg:activated": boolean }> {
  requireApi("tabs.captureVisibleTab", "taking a screenshot");
  if (params.origin !== undefined && params.origin !== "viewport") {
    throw new CommandError(
      "unsupported operation",
      `origin ${String(params.origin)} needs scroll-and-stitch, which v1 does not do; only "viewport" is supported`,
    );
  }
  const format = params.format === undefined ? "png" : params.format;
  if (format !== "png" && format !== "jpeg") {
    throw new CommandError("invalid argument", `format must be "png" or "jpeg"`);
  }

  const target = await requireTabOnly(params, "captureScreenshot");
  const tab = await chrome.tabs.get(target.tabId);
  const activated = tab.active !== true;
  if (activated) {
    await activate(target.tabId);
  }

  const quality = params.quality;
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format,
    ...(typeof quality === "number" ? { quality } : {}),
  });
  const comma = dataUrl.indexOf(",");
  return {
    data: comma === -1 ? dataUrl : dataUrl.slice(comma + 1),
    "lg:viewportOnly": true,
    "lg:activated": activated,
  };
}

async function requireTabOnly(
  params: Record<string, unknown>,
  what: string,
): Promise<Target> {
  const target = await resolveContext(params);
  if (target.frameId !== undefined) {
    throw new CommandError(
      "unsupported operation",
      `${what} works on a tab, not a frame; drop the .${target.frameId} suffix`,
    );
  }
  return target;
}

function timeoutOf(params: Record<string, unknown>): number {
  const timeout = params.timeout;
  return typeof timeout === "number" && timeout > 0 ? timeout : 30_000;
}

async function activate(tabId: number): Promise<void> {
  const tab = await chrome.tabs.update(tabId, { active: true });
  if (tab?.windowId !== undefined) {
    await chrome.windows.update(tab.windowId, { focused: true }).catch(() => undefined);
  }
}

/** Resolve once the tab reports `status: "complete"`, or reject on timeout. */
function waitForLoad(tabId: number, timeout: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const listener = (id: number, info: chrome.tabs.TabChangeInfo): void => {
      if (id === tabId && info.status === "complete") {
        settle();
      }
    };
    const deadline = setTimeout(() => {
      settle(new CommandError("unknown error", `navigation did not finish in ${timeout}ms`));
    }, timeout);

    function settle(err?: CommandError): void {
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(deadline);
      if (err) reject(err);
      else resolve();
    }

    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function tabToContext(tab: chrome.tabs.Tab): Promise<ContextInfo> {
  const tabId = tab.id ?? -1;
  return {
    context: formatContext(tabId),
    parent: null,
    url: tab.url ?? "",
    "lg:title": tab.title ?? "",
    "lg:active": tab.active,
    children: await frameChildren(tabId),
  };
}

async function frameChildren(tabId: number): Promise<ContextInfo[]> {
  if (tabId < 0) {
    return [];
  }
  // Chrome refuses this on chrome:// and similar; a tab with no readable
  // frames is still a valid context, so report it with no children.
  const frames = await chrome.webNavigation.getAllFrames({ tabId }).catch(() => null);
  if (!frames) {
    return [];
  }
  return frames
    .filter((f) => f.frameId !== 0)
    .map((f) => ({
      context: formatContext(tabId, f.frameId),
      parent: formatContext(tabId, f.parentFrameId),
      url: f.url,
      children: [],
    }));
}
