import { CommandError, optionalString } from "../protocol.ts";
import { requireApi, resolveContextOnce, type Target } from "./context.ts";

/**
 * 页面缓存（票 05，2026-09-28）：只存 text / html 正文抽取结果。
 *
 * 语义：**显式读**——读命令永远现读，缓存写是 CLI 的 text/html 包装顺手 put 的
 * 副作用；`lg:cache.get` 是唯一取回路径（`browse page cache [index]`，0=最新）。
 * 失效：导航/刷新即整页清（background.ts 监听 onUpdated 的 loading）；tab 关了
 * 整页清。淘汰：每 tab 环形留最近 5 份，全局 100 份兜底从最旧页丢。
 *
 * 扩展是 dumb storage：kind/body 对它不透明也无妨，抽取逻辑全在 CLI 侧。
 */

const KEY = "browse:pagecache";
const RING = 5;
const GLOBAL_CAP = 100;

interface Entry {
  at: number;
  kind: "text" | "html" | string;
  body: string;
}

type Store = Record<string, Entry[]>;

async function load(): Promise<Store> {
  const got = (await chrome.storage.local.get(KEY)) as Record<string, unknown>;
  const raw = got[KEY];
  return raw !== null && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Store)
    : {};
}

async function save(store: Store): Promise<void> {
  await chrome.storage.local.set({ [KEY]: store });
}

/** 全局条数超了就从「最旧条目所在页」整页丢，直到回到上限内。 */
function enforceGlobalCap(store: Store): Store {
  let total = Object.values(store).reduce((n, ring) => n + ring.length, 0);
  while (total > GLOBAL_CAP) {
    let oldestTab: string | null = null;
    let oldestAt = Number.POSITIVE_INFINITY;
    for (const [tabId, ring] of Object.entries(store)) {
      const last = ring.at(-1);
      if (last !== undefined && last.at < oldestAt) {
        oldestAt = last.at;
        oldestTab = tabId;
      }
    }
    if (oldestTab === null) {
      break;
    }
    total -= store[oldestTab]!.length;
    delete store[oldestTab];
  }
  return store;
}

async function targetTab(params: Record<string, unknown>): Promise<Target> {
  const target = await resolveContextOnce(params);
  if (target.frameId !== undefined) {
    throw new CommandError(
      "unsupported operation",
      `cache works on a tab, not a frame; drop the .${target.frameId} suffix`,
    );
  }
  return target;
}

/** `lg:cache.put`：CLI 的 text/html 读成功后顺手落一份。写失败静默——缓存是
 * 纯增益，坏存储不该让读命令报错（读结果已经打到 stdout 了）。 */
export async function cachePut(params: Record<string, unknown>): Promise<{ cached: number }> {
  requireApi("storage", "caching page reads");
  const kind = optionalString(params.kind, "kind");
  const body = optionalString(params.body, "body");
  if (kind === undefined || body === undefined) {
    throw new CommandError("invalid argument", "put needs kind and body");
  }
  const { tabId } = await targetTab(params);
  try {
    const store = await load();
    const ring = [{ at: Date.now(), kind, body }, ...(store[String(tabId)] ?? [])].slice(0, RING);
    store[String(tabId)] = ring;
    await save(enforceGlobalCap(store));
    return { cached: ring.length };
  } catch {
    return { cached: 0 };
  }
}

/** `lg:cache.get`：按 context 取该页第 index 份（0=最新，默认 0）。 */
export async function cacheGet(
  params: Record<string, unknown>,
): Promise<{ at: number; kind: string; body: string }> {
  requireApi("storage", "reading the page cache");
  const { tabId } = await targetTab(params);
  const index = typeof params.index === "number" ? params.index : 0;
  const entry = (await load())[String(tabId)]?.[index];
  if (entry === undefined) {
    throw new CommandError(
      "no such frame",
      `no cached entry ${index} for tab ${tabId} (navigation clears the cache; re-run the read)`,
    );
  }
  return entry;
}

/** `lg:cache.list`：每个有缓存的 tab 一行——条数、最新一份的时间和 kind。 */
export async function cacheList(): Promise<{
  pages: { context: string; entries: number; latest: number; kind: string }[];
}> {
  requireApi("storage", "reading the page cache");
  const store = await load();
  return {
    pages: Object.entries(store).map(([tabId, ring]) => ({
      context: tabId,
      entries: ring.length,
      latest: ring[0]?.at ?? 0,
      kind: ring[0]?.kind ?? "",
    })),
  };
}

/** 页面动了/关了就清（background.ts 的事件监听调）。永不抛。 */
export async function invalidateTab(tabId: number): Promise<void> {
  try {
    const store = await load();
    if (String(tabId) in store) {
      delete store[String(tabId)];
      await save(store);
    }
  } catch {
    // 缓存失效失败无害：下一份 put 会重新覆盖。
  }
}
