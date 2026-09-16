import { CommandError, optionalString, requireString } from "../protocol.ts";
import { requireApi } from "./context.ts";

/**
 * 只读信息类：topSites / search / dns / idle / processes / system。
 * 共同点：不写任何状态、不碰页面内容，出错就是 API 缺失或参数非法。
 */

export async function topSitesList(): Promise<{ sites: chrome.topSites.MostVisitedURL[] }> {
  requireApi("topSites", "listing the most visited sites");
  return { sites: await chrome.topSites.get() };
}

const DISPOSITIONS = ["CURRENT_TAB", "NEW_TAB", "NEW_WINDOW"] as const;

/** `chrome.search.query`：把一段文字交给默认搜索引擎，可选在哪个面打开。 */
export async function searchQuery(params: Record<string, unknown>): Promise<unknown> {
  requireApi("search", "triggering the default search engine");
  const text = optionalString(params.text, "text");
  if (text === undefined || text === "") {
    throw new CommandError("invalid argument", "text is required for lg:search.query");
  }
  const disposition = optionalString(params.disposition, "disposition");
  if (disposition !== undefined && !(DISPOSITIONS as readonly string[]).includes(disposition)) {
    throw new CommandError(
      "invalid argument",
      `disposition must be one of ${DISPOSITIONS.join(", ")}`,
    );
  }
  await chrome.search.query({
    text,
    ...(disposition === undefined ? {} : { disposition: disposition as chrome.search.Disposition }),
  });
  return { searched: text };
}

export async function dnsResolve(
  params: Record<string, unknown>,
): Promise<{ address: string; isCached: boolean }> {
  requireApi("dns", "resolving hostnames");
  const hostname = requireString(params.hostname, "hostname");
  const result = await chrome.dns.resolve(hostname);
  return { address: result.address, isCached: result.isCached };
}

export async function idleState(
  params: Record<string, unknown>,
): Promise<{ state: `${chrome.idle.IdleState}` }> {
  requireApi("idle", "checking idle state");
  const threshold = params.threshold;
  const seconds =
    threshold === undefined ? 60 : Number(threshold);
  if (!Number.isInteger(seconds) || seconds < 15 || seconds > 3600) {
    throw new CommandError("invalid argument", "threshold must be an integer in [15, 3600] seconds");
  }
  return { state: await chrome.idle.queryState(seconds) };
}

export async function processesList(): Promise<{ processes: unknown[] }> {
  requireApi("processes", "listing browser processes");
  const map = await chrome.processes.processes();
  const processes = Object.values(map).sort(
    (a, b) => (b.cpu ?? 0) - (a.cpu ?? 0),
  );
  return { processes };
}

const SYSTEM_PARTS = ["cpu", "memory", "display", "storage"] as const;
type SystemPart = (typeof SYSTEM_PARTS)[number];

/** `lg:system.info`：一次取 CPU / 内存 / 显示器 / 存储几块里的任意几块。 */
export async function systemInfo(params: Record<string, unknown>): Promise<unknown> {
  let parts: SystemPart[] = [...SYSTEM_PARTS];
  if (params.parts !== undefined) {
    const asked = params.parts;
    if (!Array.isArray(asked) || asked.some((p) => !(SYSTEM_PARTS as readonly string[]).includes(p as string))) {
      throw new CommandError("invalid argument", `parts must be a list from ${SYSTEM_PARTS.join(", ")}`);
    }
    parts = asked as SystemPart[];
  }
  const out: Record<string, unknown> = {};
  if (parts.includes("cpu")) {
    requireApi("system.cpu", "reading CPU info");
    out.cpu = await chrome.system.cpu.getInfo();
  }
  if (parts.includes("memory")) {
    requireApi("system.memory", "reading memory info");
    out.memory = await chrome.system.memory.getInfo();
  }
  if (parts.includes("display")) {
    requireApi("system.display", "reading display info");
    out.display = await chrome.system.display.getInfo();
  }
  if (parts.includes("storage")) {
    requireApi("system.storage", "reading storage info");
    out.storage = await chrome.system.storage.getInfo();
  }
  return out;
}
