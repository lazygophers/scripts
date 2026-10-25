import { CommandError, optionalString, requireString } from "../protocol.ts";
import { log } from "../telemetry.ts";
import { confirm } from "./confirm.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.userScripts` + `chrome.declarativeContent`：两种「规则式」注入。
 * userScripts 要用户先在 chrome://extensions 打开「允许用户脚本」，没开时
 * register 会抛；declarativeContent 只能按页面 URL 规则改工具栏图标。
 */

function stringArray(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.some((v) => typeof v !== "string")) {
    throw new CommandError("invalid argument", `${name} must be a non-empty list of strings`);
  }
  return value as string[];
}

/** 持久化表条目：断连 reset 后，重连由 userScriptsReconnect 按它恢复（先例 netrule 的 browse:netrules）。 */
interface StoredScript {
  id: string;
  js: string;
  matches: string[];
  world: chrome.userScripts.ExecutionWorld;
  allFrames?: boolean;
}

const REGISTRY_KEY = "browse:userscripts";

/** 本 SW 生命周期内经 lg:userScripts.register 注册的 id（断连清理用）。 */
const registeredIds = new Set<string>();

async function loadRegistry(): Promise<StoredScript[]> {
  try {
    const got = await chrome.storage?.local?.get(REGISTRY_KEY);
    const stored = got?.[REGISTRY_KEY];
    return Array.isArray(stored) ? (stored as StoredScript[]) : [];
  } catch {
    return [];
  }
}

async function persist(regs: StoredScript[]): Promise<void> {
  try {
    await chrome.storage?.local?.set({ [REGISTRY_KEY]: regs });
  } catch {
    // storage 缺席 / 写挂：表丢了只影响下次恢复，不回卷注册本身
  }
}

function toRegistration(s: StoredScript) {
  return {
    id: s.id,
    js: [{ code: s.js }],
    matches: s.matches,
    world: s.world,
    ...(s.allFrames === undefined ? {} : { allFrames: s.allFrames }),
  };
}

/**
 * bridge 断开：本扩展注册的 userScripts 全部撤销（先例 netrule 的 netRuleReset）。
 * 注册参数留在持久化表里，重连由 userScriptsReconnect 恢复。reset() 清空全部
 * userScripts（含用户手动建的），所以只在注册过时才调；表为空（从未注册）就不动
 * 任何东西——SW 重启后 registeredIds 虽空，表还在，残留照样被清掉。
 */
export async function userScriptsDisconnect(): Promise<void> {
  registeredIds.clear();
  const regs = await loadRegistry();
  if (regs.length === 0) {
    return;
  }
  await chrome.userScripts?.reset().catch(() => {});
}

/**
 * bridge 重连成功（background 的 connected 回调调）：按持久化表重注册。
 * 注册时已过确认策略，恢复不重复问；单条失败记 telemetry，不打断其余条目。
 */
export async function userScriptsReconnect(): Promise<void> {
  const regs = await loadRegistry();
  if (regs.length === 0) {
    return;
  }
  const ok: string[] = [];
  for (const s of regs) {
    try {
      await chrome.userScripts?.register([toRegistration(s)]);
      registeredIds.add(s.id);
      ok.push(s.id);
    } catch (err) {
      log("userscripts.reregister.failed", "error", {
        id: s.id,
        error: String((err as Error)?.message ?? err),
      });
    }
  }
  if (ok.length > 0) {
    log("userscripts.reregistered", "info", { ids: ok });
  }
}

export async function userScriptsRegister(
  params: Record<string, unknown>,
): Promise<{ registered: string }> {
  requireApi("userScripts", "registering user scripts");
  const id = requireString(params.name, "name");
  const js = requireString(params.js, "js", ", the script source");
  const matches = stringArray(params.matches, "matches", );
  const world = optionalString(params.world, "world") ?? "USER_SCRIPT";
  if (world !== "USER_SCRIPT" && world !== "MAIN") {
    throw new CommandError("invalid argument", "world must be USER_SCRIPT or MAIN");
  }
  await confirm({
    action: "registerUserScript",
    method: "lg:userScripts.register",
    url: null,
  });
  const stored: StoredScript = {
    id,
    js,
    matches,
    world: world as chrome.userScripts.ExecutionWorld,
    ...(params.allFrames === undefined ? {} : { allFrames: params.allFrames === true }),
  };
  await chrome.userScripts.register([toRegistration(stored)]);
  registeredIds.add(id);
  void persist([...(await loadRegistry()).filter((r) => r.id !== id), stored]);
  return { registered: id };
}

export async function userScriptsList(): Promise<{ scripts: unknown[] }> {
  requireApi("userScripts", "listing user scripts");
  return { scripts: await chrome.userScripts.getScripts() };
}

export async function userScriptsUnregister(
  params: Record<string, unknown>,
): Promise<{ unregistered: string }> {
  requireApi("userScripts", "unregistering user scripts");
  const id = requireString(params.name, "name");
  await chrome.userScripts.unregister({ id });
  registeredIds.delete(id);
  void persist((await loadRegistry()).filter((r) => r.id !== id));
  return { unregistered: id };
}

export async function userScriptsReset(): Promise<{ reset: true }> {
  requireApi("userScripts", "resetting user scripts");
  await chrome.userScripts.reset();
  registeredIds.clear();
  void persist([]); // 显式 reset 连表一起清：重连不恢复
  return { reset: true };
}

/** `lg:userScripts.world`：MAIN world 的 CSP / 可用消息 API。 */
export async function userScriptsWorld(
  params: Record<string, unknown>,
): Promise<{ messaging: boolean; csp: string | null }> {
  requireApi("userScripts.configureWorld", "configuring the user script world");
  const messaging = params.messaging === true;
  const csp = optionalString(params.csp, "csp") ?? null;
  await chrome.userScripts.configureWorld({
    messaging,
    ...(csp === null ? {} : { csp }),
  });
  return { messaging, csp };
}

/**
 * `lg:declContent.setRules`：整组替换。条件只支持 URL 前缀 / glob（Chrome 的
 * PageUrlMatcher 两项），动作固定是「匹配时让工具栏图标亮起来」（ShowAction）。
 */
export async function declContentSetRules(
  params: Record<string, unknown>,
): Promise<{ rules: number }> {
  requireApi("declarativeContent", "setting declarative page rules");
  const raw = params.rules;
  if (!Array.isArray(raw) || raw.length === 0) {
    throw new CommandError("invalid argument", "rules must be a non-empty list of {urlPrefix?|urlMatches?}");
  }
  const conditions = raw.map((rule) => {
    const r = rule as Record<string, unknown>;
    const urlPrefix = optionalString(r.urlPrefix, "rules[].urlPrefix");
    const urlMatches = optionalString(r.urlMatches, "rules[].urlMatches");
    if (urlPrefix === undefined && urlMatches === undefined) {
      throw new CommandError("invalid argument", "each rule needs urlPrefix or urlMatches");
    }
    return new chrome.declarativeContent.PageUrlMatcher({
      ...(urlPrefix === undefined ? {} : { urlPrefix }),
      ...(urlMatches === undefined ? {} : { urlMatches }),
    });
  });
  await chrome.declarativeContent.onPageChanged.removeRules(undefined);
  await chrome.declarativeContent.onPageChanged.addRules(
    conditions.map((pageUrl, i) => ({
      id: `rule-${i + 1}`,
      conditions: [pageUrl],
      actions: [new chrome.declarativeContent.ShowAction()],
    })),
  );
  return { rules: conditions.length };
}

export async function declContentClear(): Promise<{ cleared: true }> {
  requireApi("declarativeContent", "clearing declarative page rules");
  await chrome.declarativeContent.onPageChanged.removeRules(undefined);
  return { cleared: true };
}
