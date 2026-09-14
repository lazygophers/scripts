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
