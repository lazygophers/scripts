import { CommandError, asString } from "../protocol.ts";

/** A resolved target: a tab, optionally narrowed to one of its frames. */
export interface Target {
  tabId: number;
  frameId: number | undefined;
}

/**
 * Context ids are `<tabId>` for a tab and `<tabId>.<frameId>` for a frame.
 * Frame 0 *is* the tab, so it formats as the bare tab id — the inverse of
 * `parseContext`.
 */
export function formatContext(tabId: number, frameId?: number): string {
  return frameId === undefined || frameId === 0 ? String(tabId) : `${tabId}.${frameId}`;
}

/** Context ids are `<tabId>` for a tab and `<tabId>.<frameId>` for a frame. */
export function parseContext(context: string): Target {
  const [tabPart, framePart] = context.split(".", 2);
  const tabId = Number(tabPart);
  if (!Number.isInteger(tabId)) {
    throw new CommandError("no such frame", `bad context id ${context}`);
  }
  if (framePart === undefined) {
    return { tabId, frameId: undefined };
  }
  const frameId = Number(framePart);
  if (!Number.isInteger(frameId)) {
    throw new CommandError("no such frame", `bad context id ${context}`);
  }
  return { tabId, frameId };
}

/** Shell-style glob, anchored. Only `*` and `?` are special. */
export function globToRegExp(glob: string): RegExp {
  const body = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${body}$`);
}

/**
 * Target context selection, spec 6.5: `context` > `matchUrl` glob (several
 * matches is an error, not a coin flip) > the active tab of the current window.
 *
 * Accepts the params object of any command, so every handler shares one rule.
 */
export async function resolveContext(params: Record<string, unknown>): Promise<Target> {
  const { context, matchUrl } = params as { context?: unknown; matchUrl?: unknown };

  if (context !== undefined) {
    return parseContext(asString(context, "context", ", a context id"));
  }

  if (matchUrl !== undefined) {
    const glob = asString(matchUrl, "matchUrl", ", a shell-style glob");
    const pattern = globToRegExp(glob);
    const hits = (await chrome.tabs.query({})).filter((tab) => pattern.test(tab.url ?? ""));
    if (hits.length === 0) {
      throw new CommandError("no such frame", `no browsing context matches ${glob}`);
    }
    if (hits.length > 1) {
      const urls = hits.map((tab) => `${tab.id}=${tab.url ?? ""}`).join(", ");
      throw new CommandError(
        "invalid argument",
        `matchUrl ${glob} matches ${hits.length} contexts (${urls}); narrow it or pass context`,
      );
    }
    const only = hits[0];
    if (only?.id === undefined) {
      throw new CommandError("no such frame", `context matching ${glob} has no id`);
    }
    return { tabId: only.id, frameId: undefined };
  }

  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.id === undefined) {
    throw new CommandError("no such frame", "no active tab");
  }
  return { tabId: active.id, frameId: undefined };
}

/**
 * `lg:context.url` — the daemon asking which page a command would land on.
 *
 * Not a capability: the CLI cannot spell it. `input.*` and `script.*` carry no
 * url in their params, so the daemon has no way to apply `deny_domains`
 * (spec 4.3) to them without asking. The rule for *which* page is right here in
 * `resolveContext`; copying it into Python would drift the first time it
 * changes.
 */
export async function contextUrl(
  params: Record<string, unknown>,
): Promise<{ url: string | null }> {
  return { url: await targetUrl(await resolveContext(params)) };
}

/** The page URL of a target, for the audit/confirm record. Never throws. */
export async function targetUrl(target: Target): Promise<string | null> {
  const tab = await chrome.tabs.get(target.tabId).catch(() => null);
  return tab?.url ?? null;
}

/**
 * BiDi 把 context 嵌在 `target` 里（`script.*`），CLI 拍平了传。两种都认，拍平成
 * 同一个形状 —— dispatch 和 handler 才会解析出同一个目标（策略目标，CONTEXT.md）。
 */
export function flattenTarget(params: Record<string, unknown>): Record<string, unknown> {
  const nested = params.target;
  if (nested !== undefined && typeof nested === "object" && nested !== null) {
    return { ...params, ...(nested as Record<string, unknown>) };
  }
  return params;
}

const contextCache = new Map<string, Promise<Target>>();

function contextCacheKey(params: Record<string, unknown>): string {
  const flat = flattenTarget(params);
  const c = typeof flat.context === "string" ? flat.context : "";
  const m = typeof flat.matchUrl === "string" ? flat.matchUrl : "";
  return `${c}\u0000${m}`;
}

/**
 * 一条指令只解析一次 context：dispatch 先解析（做 deny/feature 的域名裁决），
 * handler 再调用拿到的是**同一份缓存** —— 回调式共享，handler 签名不变。
 *
 * 缓存键是 context/matchUrl 的取值，空键代表「当前活动标签页」，指令结束
 * （`dropContextCache`）就删，绝不跨指令复用。
 */
export function resolveContextOnce(params: Record<string, unknown>): Promise<Target> {
  const key = contextCacheKey(params);
  let hit = contextCache.get(key);
  if (hit === undefined) {
    hit = resolveContext(flattenTarget(params));
    contextCache.set(key, hit);
    void hit.catch(() => contextCache.delete(key)); // 失败不缓存，下次重跑拿真错误
  }
  return hit;
}

/** 指令收尾（dispatch 的 finally）：context 缓存不活得比一条指令长。 */
export function dropContextCache(params: Record<string, unknown>): void {
  contextCache.delete(contextCacheKey(params));
}

/**
 * Cross-browser capability check, spec 5.5: explicit refusal, never a silent
 * substitute implementation. `path` is dotted under `chrome`, e.g. `downloads`
 * or `tabs.captureVisibleTab`.
 */
export function requireApi(path: string, why: string): void {
  let node: unknown = globalThis.chrome as unknown;
  for (const part of path.split(".")) {
    node = (node as Record<string, unknown> | undefined)?.[part];
  }
  if (node === undefined || node === null) {
    throw new CommandError(
      "unsupported operation",
      `chrome.${path} is not available in this browser, so ${why} cannot be done here`,
    );
  }
}
