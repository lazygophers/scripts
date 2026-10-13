import { CommandError, requireString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { globToRegExp, requireApi, resolveContextOnce, targetUrl } from "./context.ts";
import { acquireDebugger, forgetDebugger, releaseDebugger } from "./dbg.ts";
import { isOwnTab } from "./ownership.ts";

/**
 * `lg:net.route*` — 请求拦截：命中 URL 模式的请求可以 mock 假响应或直接
 * abort（2026-10-13，browse-enhance 票 03）。
 *
 * MV3 的 webRequest 是只读的（见 network.ts 的说明），body 级拦截只有
 * `chrome.debugger` 的 Fetch domain 一条路——console 采集（handlers/console.ts）
 * 已经用同一权限开过先例。与 declarativeNetRequest 的取舍：dNR 能做
 * redirect/block 但拿不到 body，mock 要的第二条路总是绕不开 Fetch domain，
 * 索性一条机制管三种动作，也少一个 manifest 权限。
 *
 * 安全口径与 console 采集同源：
 * - **绝不自动 attach**：只有显式 `net route add` 解析到的那个 owned tab 才
 *   attach（与共享管理器 dbg.ts 合租，console 在同 tab 也不冲突）。
 * - `add` 是高危动作 `netRoute`：走确认策略。
 * - tab 关闭 / 撤出归属组 / bridge 断开，规则随 attach 一起摘。
 * - 规则持久化在 `chrome.storage.local["browse:netrules"]`，服务重启后由
 *   background 的 `netRuleArm()` 重新武装（debugger attach 不能跨重启存活）。
 */
export type NetRouteAction = "mock" | "abort";

export interface NetRule {
  id: string;
  tabId: number;
  /** 展示用原始 glob；匹配走 regex，Fetch.enable 的 urlPattern 走原始串。 */
  pattern: string;
  regex: RegExp;
  action: NetRouteAction;
  status: number;
  headers: Array<{ name: string; value: string }>;
  /** mock body（UTF-8 → base64）。abort 规则没有。 */
  bodyBase64?: string;
  /** 本次 attach 期命中数；不持久化，重启清零。 */
  hits: number;
}

interface StoredRule {
  id: string;
  tabId: number;
  pattern: string;
  action: NetRouteAction;
  status: number;
  headers: Array<{ name: string; value: string }>;
  bodyBase64?: string;
}

const RULES_KEY = "browse:netrules";
/** mock body 上限：存 storage、过 daemon、进审计都要走一遍，别拿它当网盘。 */
const MAX_BODY_BYTES = 1024 * 1024;

const rules = new Map<string, NetRule>();
let counter = 0;
let listenersAttached = false;

function pick(rule: NetRule): StoredRule & { hits: number } {
  const { regex: _regex, ...rest } = rule;
  return { ...rest };
}

function persist(): Promise<void> {
  const stored: StoredRule[] = [...rules.values()].map(({ regex: _r, hits: _h, ...rest }) => rest);
  return chrome.storage.local.set({ [RULES_KEY]: stored }).catch(() => {}) as Promise<void>;
}

async function load(): Promise<void> {
  const got = await chrome.storage.local.get(RULES_KEY).catch(() => ({}) as Record<string, unknown>);
  const stored = got?.[RULES_KEY];
  if (!Array.isArray(stored)) {
    return;
  }
  for (const raw of stored as StoredRule[]) {
    if (typeof raw?.id !== "string" || typeof raw?.tabId !== "number") {
      continue;
    }
    const numeric = Number(raw.id.split("-")[1]);
    if (Number.isFinite(numeric) && numeric > counter) {
      counter = numeric;
    }
    rules.set(raw.id, {
      ...raw,
      headers: Array.isArray(raw.headers) ? raw.headers : [],
      regex: globToRegExp(raw.pattern),
      hits: 0,
    });
  }
}

/** 该 tab 当前全部规则的 pattern 并集，喂给 Fetch.enable。 */
function patternsOf(tabId: number): Array<{ urlPattern: string }> {
  return [...new Set([...rules.values()].filter((r) => r.tabId === tabId).map((r) => r.pattern))]
    .map((urlPattern) => ({ urlPattern }));
}

async function armTab(tabId: number, acquire: boolean): Promise<void> {
  // 引用计数按「这个 tab 有没有规则」记，不按规则条数：两条规则一个 hold，
  // 最后一条删掉才 detach（console 采集与它合租同一个 attach）。
  if (acquire) {
    await acquireDebugger(tabId);
  }
  try {
    await chrome.debugger.sendCommand({ tabId }, "Fetch.enable",
      patternsOf(tabId).length > 0 ? { patterns: patternsOf(tabId) } : {});
  } catch (err) {
    await releaseDebugger(tabId).catch(() => {});
    throw new CommandError(
      "unsupported operation",
      `Fetch.enable failed on tab ${tabId}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

async function disarmTab(tabId: number): Promise<void> {
  try {
    await chrome.debugger.sendCommand({ tabId }, "Fetch.disable");
  } catch {
    // 已被强制 detach（DevTools 抢走）：Fetch 域随 attach 一起没了
  }
  await releaseDebugger(tabId).catch(() => {});
}

function attachDebuggerListeners(): void {
  if (listenersAttached) {
    return;
  }
  listenersAttached = true;
  chrome.debugger.onEvent.addListener((source, method, params) => {
    if (method !== "Fetch.requestPaused" || source.tabId === undefined) {
      return;
    }
    const tabId = source.tabId;
    const ev = (params ?? {}) as { requestId?: string; request?: { url?: string } };
    const requestId = ev.requestId;
    const url = ev.request?.url ?? "";
    if (requestId === undefined) {
      return;
    }
    const rule = [...rules.values()].find((r) => r.tabId === tabId && r.regex.test(url));
    if (rule === undefined) {
      // 这个 tab 的别的规则的 pattern 把请求带停了——放行
      void chrome.debugger
        .sendCommand({ tabId }, "Fetch.continueRequest", { requestId })
        .catch(() => {});
      return;
    }
    rule.hits += 1;
    if (rule.action === "abort") {
      void chrome.debugger
        .sendCommand({ tabId }, "Fetch.failRequest", { requestId, errorReason: "Aborted" })
        .catch(() => {});
      return;
    }
    const headers = rule.headers.length > 0
      ? rule.headers
      : rule.bodyBase64 !== undefined
        ? [{ name: "content-type", value: "text/plain; charset=utf-8" }]
        : [];
    void chrome.debugger
      .sendCommand({ tabId }, "Fetch.fulfillRequest", {
        requestId,
        responseCode: rule.status,
        responseHeaders: headers,
        ...(rule.bodyBase64 !== undefined ? { body: rule.bodyBase64 } : {}),
      })
      .catch(() => {});
  });
  chrome.debugger.onDetach.addListener((source) => {
    if (source.tabId !== undefined) {
      forgetDebugger(source.tabId);
    }
  });
}

// ---------------------------------------------------------------- handlers

async function requireTab(params: Record<string, unknown>): Promise<number> {
  const target = await resolveContextOnce(params);
  if (target.frameId !== undefined) {
    throw new CommandError(
      "unsupported operation",
      `net routes work on a tab, not a frame; drop the .${target.frameId} suffix`,
    );
  }
  return target.tabId;
}

function base64Of(body: string): string {
  const bytes = new TextEncoder().encode(body);
  let bin = "";
  for (const b of bytes) {
    bin += String.fromCharCode(b);
  }
  return btoa(bin);
}

/** `lg:net.routeAdd`：加一条规则并对目标 tab 武装 Fetch 域。 */
export async function netRouteAdd(
  params: Record<string, unknown>,
): Promise<StoredRule & { hits: number }> {
  requireApi("debugger", "intercepting requests");
  const pattern = requireString(params.pattern, "pattern", ", a shell-style URL glob like 'https://api.test/v1/*'");
  const action = params.action === "abort" ? "abort" : params.action === "mock" ? "mock" : null;
  if (action === null) {
    throw new CommandError("invalid argument", `action must be "mock" or "abort"`);
  }
  const status = params.status === undefined ? 200 : params.status;
  if (typeof status !== "number" || !Number.isInteger(status) || status < 100 || status > 599) {
    throw new CommandError("invalid argument", "status must be an integer HTTP status code");
  }
  const headers = params.headers === undefined ? [] : params.headers;
  if (!Array.isArray(headers)
      || !headers.every((h) => h && typeof h === "object"
        && typeof (h as { name?: unknown }).name === "string"
        && typeof (h as { value?: unknown }).value === "string")) {
    throw new CommandError("invalid argument", "headers must be an array of {name, value}");
  }
  let bodyBase64: string | undefined;
  if (params.body !== undefined) {
    if (typeof params.body !== "string") {
      throw new CommandError("invalid argument", "body must be a string");
    }
    const bytes = new TextEncoder().encode(params.body).length;
    if (bytes > MAX_BODY_BYTES) {
      throw new CommandError(
        "invalid argument",
        `mock body is ${bytes} bytes, over the ${MAX_BODY_BYTES} limit`,
      );
    }
    bodyBase64 = base64Of(params.body);
  }

  const tabId = await requireTab(params);
  const url = await targetUrl({ tabId, frameId: undefined });
  await confirm({ action: "netRoute", method: "lg:net.routeAdd", url });

  attachDebuggerListeners();
  counter += 1;
  const rule: NetRule = {
    id: `rule-${counter}`,
    tabId,
    pattern,
    regex: globToRegExp(pattern),
    action,
    status,
    headers: headers as Array<{ name: string; value: string }>,
    ...(bodyBase64 !== undefined ? { bodyBase64 } : {}),
    hits: 0,
  };
  const firstForTab = ![...rules.values()].some((r) => r.tabId === tabId);
  rules.set(rule.id, rule);
  try {
    await armTab(tabId, firstForTab);
  } catch (err) {
    rules.delete(rule.id);
    await persist();
    throw err;
  }
  await persist();
  return pick(rule);
}

/** `lg:net.routeRemove`：删一条；该 tab 没剩规则就拆 Fetch 域。 */
export async function netRouteRemove(
  params: Record<string, unknown>,
): Promise<{ removed: string }> {
  const id = requireString(params.id, "id", ", a rule id from net route list");
  const rule = rules.get(id);
  if (rule === undefined) {
    throw new CommandError("invalid argument", `no such rule ${id}`);
  }
  rules.delete(id);
  if (![...rules.values()].some((r) => r.tabId === rule.tabId)) {
    await disarmTab(rule.tabId);
  }
  await persist();
  return { removed: id };
}

/** `lg:net.routeList`：全部规则（含命中数）。 */
export async function netRouteList(): Promise<{ rules: Array<StoredRule & { hits: number }> }> {
  return { rules: [...rules.values()].map(pick) };
}

/** `lg:net.routeClear`：全删、全拆。 */
export async function netRouteClear(): Promise<{ removed: number }> {
  const tabs = new Set([...rules.values()].map((r) => r.tabId));
  const removed = rules.size;
  rules.clear();
  for (const tabId of tabs) {
    await disarmTab(tabId);
  }
  await persist();
  return { removed };
}

// ---------------------------------------------------------------- 生命周期

/** tab 关了：摘掉它的规则（background.ts 的 onRemoved 调）。 */
export function netRuleForget(tabId: number): void {
  const had = [...rules.values()].some((r) => r.tabId === tabId);
  if (!had) {
    return;
  }
  for (const [id, rule] of rules) {
    if (rule.tabId === tabId) {
      rules.delete(id);
    }
  }
  forgetDebugger(tabId);
  void persist();
}

/** bridge 断开：全摘（background.ts 的状态回调调）。 */
export function netRuleReset(): void {
  const tabs = new Set([...rules.values()].map((r) => r.tabId));
  rules.clear();
  for (const tabId of tabs) {
    forgetDebugger(tabId);
    void chrome.debugger?.detach({ tabId }).catch(() => {});
  }
  void persist();
}

/**
 * 服务重启后重新武装持久化的规则（background 启动时调）：规则还在 storage，
 * 但 debugger attach 不能跨重启——逐 tab 检查存在且仍归属，才重新 attach。
 */
export async function netRuleArm(): Promise<void> {
  await load();
  for (const tabId of new Set([...rules.values()].map((r) => r.tabId))) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (!(await isOwnTab(tab))) {
        for (const [id, rule] of rules) {
          if (rule.tabId === tabId) {
            rules.delete(id);
          }
        }
        continue;
      }
      attachDebuggerListeners();
      await armTab(tabId, true);
    } catch {
      // tab 已不在：规则作废
      for (const [id, rule] of rules) {
        if (rule.tabId === tabId) {
          rules.delete(id);
        }
      }
    }
  }
  await persist();
}

/** 测试钩子：回到「没规则、没监听」的初始状态。 */
export function resetNetRules(): void {
  const tabs = new Set([...rules.values()].map((r) => r.tabId));
  rules.clear();
  counter = 0;
  listenersAttached = false;
  // 引用计数（dbg.ts）是模块级状态，不随夹具重置——测试间会越积越多，
  // 让后续 acquire 跳过 attach。这里一并作废。
  for (const tabId of tabs) {
    forgetDebugger(tabId);
    void chrome.debugger?.detach({ tabId }).catch(() => {});
  }
}
