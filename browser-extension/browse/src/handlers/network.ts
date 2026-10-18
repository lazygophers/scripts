import { emitEvent } from "../events.ts";
import { CommandError, optionalString } from "../protocol.ts";
import { globToRegExp, requireApi } from "./context.ts";
import { isOwnTab } from "./ownership.ts";

/**
 * `network.subscribe` / `network.unsubscribe`: **metadata only** (spec 5.4 #3).
 *
 * MV3's `webRequest` is observational — no blocking, and no access to request
 * or response bodies at all. Reading or rewriting a body needs
 * `chrome.debugger`'s Fetch domain, which spec 5.3 rules out. So the events
 * carry URL, method, resource type, status, timing and headers, and nothing
 * else. There is no `body` field to be disappointed by.
 *
 * Emits one `network.responseCompleted` per finished request and one
 * `network.fetchError` per failed one, each tagged with the subscription ids
 * that matched.
 */
interface Subscription {
  id: string;
  matchUrl: string | null;
  pattern: RegExp | null;
  types: string[] | null;
}

interface Pending {
  url: string;
  method: string;
  type: string;
  startedAt: number;
  tabId: number;
  requestHeaders: chrome.webRequest.HttpHeader[];
}

const subscriptions = new Map<string, Subscription>();
const pending = new Map<string, Pending>();
let counter = 0;

export async function networkSubscribe(
  params: Record<string, unknown>,
): Promise<{ subscription: string; "lg:metadataOnly": true }> {
  requireApi("webRequest", "observing network traffic");
  const { types } = params;
  const matchUrl = optionalString(params.matchUrl, "matchUrl", ", a shell-style glob");
  if (types !== undefined && !Array.isArray(types)) {
    throw new CommandError("invalid argument", "types must be an array of resource types");
  }

  counter += 1;
  const id = `net-${counter}`;
  subscriptions.set(id, {
    id,
    matchUrl: matchUrl ?? null,
    pattern: matchUrl === undefined ? null : globToRegExp(matchUrl),
    types: types === undefined ? null : (types as unknown[]).map(String),
  });
  attach();
  return { subscription: id, "lg:metadataOnly": true };
}

export async function networkUnsubscribe(
  params: Record<string, unknown>,
): Promise<{ removed: string[] }> {
  const id = optionalString(params.subscription, "subscription", ", a subscription id");
  const removed = id === undefined ? [...subscriptions.keys()] : [id];
  for (const key of removed) {
    if (!subscriptions.delete(key)) {
      throw new CommandError("invalid argument", `no such subscription ${key}`);
    }
  }
  if (subscriptions.size === 0) {
    detach();
    pending.clear();
  }
  return { removed };
}

/** Exported for the panel/brake path: drop every subscription at once. */
export function networkReset(): void {
  subscriptions.clear();
  pending.clear();
  detach();
}

function matching(url: string, type: string): string[] {
  const hits: string[] = [];
  for (const sub of subscriptions.values()) {
    if (sub.pattern !== null && !sub.pattern.test(url)) continue;
    if (sub.types !== null && !sub.types.includes(type)) continue;
    hits.push(sub.id);
  }
  return hits;
}

// 归属过滤（2026-10-18）：订阅只看自己组里 tab 的流量。判定走 ownership 的
// isOwnTab（组员身份带 2 秒 TTL 缓存），撤销归属最迟 2 秒生效。
async function isOwnTabId(tabId: number): Promise<boolean> {
  if (tabId < 0) {
    return false; // 非 tab 请求（service worker、预取等）没有归属
  }
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return tab !== null && isOwnTab(tab);
}

async function matchingOwned(url: string, type: string, tabId: number): Promise<string[]> {
  // 先过订阅正则（纯同步、绝大多数请求在这里返回），归属 IPC 只为命中者付
  const subs = matching(url, type);
  if (subs.length === 0 || !(await isOwnTabId(tabId))) {
    return [];
  }
  return subs;
}

// webRequest 的监听签名是同步的（可返回 BlockingResponse）：归属过滤是异步的，
// 包一层同步壳，内部异步跑，从不拦截所以返回 undefined。
const onBeforeRequest = (details: chrome.webRequest.OnBeforeRequestDetails): undefined => {
  void handleBeforeRequest(details);
  return undefined;
};

const handleBeforeRequest = async (details: chrome.webRequest.OnBeforeRequestDetails): Promise<void> => {
  if ((await matchingOwned(details.url, details.type, details.tabId)).length === 0) return;
  pending.set(details.requestId, {
    url: details.url,
    method: details.method,
    type: details.type,
    startedAt: details.timeStamp,
    tabId: details.tabId,
    requestHeaders: [],
  });
};

const onSendHeaders = (details: chrome.webRequest.OnBeforeSendHeadersDetails): void => {
  const entry = pending.get(details.requestId);
  if (entry) entry.requestHeaders = details.requestHeaders ?? [];
};

const onCompleted = async (details: chrome.webRequest.OnCompletedDetails): Promise<void> => {
  const entry = pending.get(details.requestId);
  pending.delete(details.requestId);
  const subs = await matchingOwned(details.url, details.type, details.tabId);
  if (subs.length === 0) return;
  emitEvent("network.responseCompleted", {
    subscriptions: subs,
    request: {
      request: details.requestId,
      url: details.url,
      method: details.method,
      type: details.type,
      context: details.tabId >= 0 ? String(details.tabId) : null,
      headers: headers(entry?.requestHeaders),
      timestamp: entry?.startedAt ?? details.timeStamp,
    },
    response: {
      status: details.statusCode,
      fromCache: details.fromCache,
      headers: headers(details.responseHeaders),
    },
    "lg:durationMs": entry === undefined ? null : details.timeStamp - entry.startedAt,
    "lg:metadataOnly": true,
  });
};

const onErrorOccurred = async (details: chrome.webRequest.OnErrorOccurredDetails): Promise<void> => {
  const entry = pending.get(details.requestId);
  pending.delete(details.requestId);
  const subs = await matchingOwned(details.url, details.type, details.tabId);
  if (subs.length === 0) return;
  emitEvent("network.fetchError", {
    subscriptions: subs,
    request: {
      request: details.requestId,
      url: details.url,
      method: details.method,
      type: details.type,
      context: details.tabId >= 0 ? String(details.tabId) : null,
      headers: headers(entry?.requestHeaders),
      timestamp: entry?.startedAt ?? details.timeStamp,
    },
    errorText: details.error,
  });
};

function headers(list: chrome.webRequest.HttpHeader[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const header of list ?? []) {
    out[header.name] = header.value ?? "";
  }
  return out;
}

let attached = false;
const ALL: chrome.webRequest.RequestFilter = { urls: ["<all_urls>"] };

function attach(): void {
  if (attached) return;
  attached = true;
  chrome.webRequest.onBeforeRequest.addListener(onBeforeRequest, ALL);
  chrome.webRequest.onSendHeaders.addListener(onSendHeaders, ALL, ["requestHeaders"]);
  chrome.webRequest.onCompleted.addListener(onCompleted, ALL, ["responseHeaders"]);
  chrome.webRequest.onErrorOccurred.addListener(onErrorOccurred, ALL);
}

function detach(): void {
  if (!attached) return;
  attached = false;
  chrome.webRequest.onBeforeRequest.removeListener(onBeforeRequest);
  chrome.webRequest.onSendHeaders.removeListener(onSendHeaders);
  chrome.webRequest.onCompleted.removeListener(onCompleted);
  chrome.webRequest.onErrorOccurred.removeListener(onErrorOccurred);
}
