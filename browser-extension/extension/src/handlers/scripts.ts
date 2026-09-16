import { CommandError, optionalString, requireString } from "../protocol.ts";
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
  await chrome.userScripts.register([
    {
      id,
      js: [{ code: js }],
      matches,
      world: world as chrome.userScripts.ExecutionWorld,
      ...(params.allFrames === undefined ? {} : { allFrames: params.allFrames === true }),
    },
  ]);
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
  return { unregistered: id };
}

export async function userScriptsReset(): Promise<{ reset: true }> {
  requireApi("userScripts", "resetting user scripts");
  await chrome.userScripts.reset();
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
