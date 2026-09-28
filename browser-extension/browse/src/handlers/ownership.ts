import { CommandError } from "../protocol.ts";

/**
 * 页面归属（票 02 设计，2026-09-28）。
 *
 * 规则一句话：**组成员身份 = 归属真源**。在 browse 专属组里的标签页就是
 * 「自己的页面」，页面方法只许落在它上面；用户把标签拖进组即接管、拖出即放走。
 *
 * 为什么懒判而不是监听事件：`chrome.tabGroups` 没有成员变更事件，监听拖拽
 * 不可靠。归属在每条命令到达时现场查 `tab.groupId`，零监听、永不漂移。
 *
 * 两层存储：
 * - `chrome.storage.session["browse:own"]` = `{groupId}`，浏览器会话内有效，
 *   SW 睡醒不丢；浏览器重启清零 → 走重建路径。
 * - `chrome.storage.local["browse:ownership"]` = origin+path 数组（LRU 上限
 *   50），唯一用途是浏览器重启后按 URL 把现存 tab 认领回组。
 *
 * 当前窗口 + 单组：没有 `browse` 组时，调用方先在当前窗口创建目标 tab，
 * 再以该 tab 创建组；不创建独立窗口。已有组时，目标 tab 在组所在窗口创建。
 */

const GROUP_TITLE = "browse";
const SESSION_KEY = "browse:own";
const REGISTRY_KEY = "browse:ownership";
/** 用户 2026-09-28 第 3 轮拍的：上限 50 条。 */
const REGISTRY_CAP = 50;

/** URL 归一成 origin+path（丢查询串和锚点）——认领粒度，第 3 轮拍板。 */
export function canonical(url: string | null | undefined): string | null {
  if (typeof url !== "string" || url === "") {
    return null;
  }
  try {
    const u = new URL(url);
    // 不透明 origin（about:blank、data:）没有稳定的 origin 可比，认领不了
    if (u.origin === "null" || u.origin === "") {
      return null;
    }
    return u.origin + u.pathname;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- 登记表

export async function registrySet(): Promise<Set<string>> {
  const got = await chrome.storage.local.get(REGISTRY_KEY);
  const raw = (got as Record<string, unknown>)[REGISTRY_KEY];
  return new Set(Array.isArray(raw) ? raw.filter((u): u is string => typeof u === "string") : []);
}

/**
 * 记一页「是我们开的/收编的」。LRU 前插；命中表头就不写，省掉每条命令一次
 * `storage.set`。写失败静默——登记表只服务重启认领，坏一条不该炸命令。
 */
export async function recordVisit(url: string | null | undefined): Promise<void> {
  const c = canonical(url);
  if (c === null) {
    return;
  }
  try {
    const got = (await chrome.storage.local.get(REGISTRY_KEY)) as Record<string, unknown>;
    const raw = got[REGISTRY_KEY];
    const urls = Array.isArray(raw) ? raw.filter((u): u is string => typeof u === "string") : [];
    if (urls[0] === c) {
      return;
    }
    const next = [c, ...urls.filter((u) => u !== c)].slice(0, REGISTRY_CAP);
    await chrome.storage.local.set({ [REGISTRY_KEY]: next });
  } catch {
    // 存储坏了：认领会退化，权限判定不受影响（那是 groupId 的事）。
  }
}

// ---------------------------------------------------------------- 专属组

interface OwnGroup {
  groupId: number;
  windowId: number;
}

/** SW 生命周期内的缓存。每次用前都花一个 `tabGroups.get` 校验——组可能被用户
 * 随手关掉，校验失败就清缓存走重建，权限判定永远不拿死 id 碰运气。 */
let cached: OwnGroup | null = null;
/** 重建期间的单飞 promise：并发命令只建一个窗口。 */
let building: Promise<OwnGroup> | null = null;
/** 测试钩子：清掉缓存，让每个用例从「还没查过」开始。 */
export function resetOwnership(): void {
  cached = null;
  building = null;
}

async function remember(group: OwnGroup): Promise<OwnGroup> {
  cached = group;
  try {
    await chrome.storage.session.set({ [SESSION_KEY]: { groupId: group.groupId } });
  } catch {
    // session 存不下（老浏览器）：SW 活着期间靠 cached，重启后重建。
  }
  return group;
}

/** 找一个现存的 `browse` 组当自己的，没有就用 seedTabId 在其当前窗口建组。 */
async function findOrCreate(seedTabId?: number): Promise<OwnGroup> {
  const found = await chrome.tabGroups.query({ title: GROUP_TITLE });
  if (found.length > 0) {
    const group = found[0]!;
    return { groupId: group.id, windowId: group.windowId };
  }
  let tabId = seedTabId;
  if (tabId === undefined) {
    const owned = await registrySet();
    const tabs = await chrome.tabs.query({});
    tabId = tabs.find((tab) => {
      const url = canonical(tab.url);
      return tab.id !== undefined && url !== null && owned.has(url);
    })?.id;
  }
  if (tabId === undefined) {
    throw new CommandError(
      "no such frame",
      "no browse group; open a page with `browse tab open` or adopt one with `browse tab adopt`",
    );
  }
  const tab = await chrome.tabs.get(tabId);
  if (tab.windowId === undefined) {
    throw new CommandError("unknown error", `tab ${seedTabId} has no window`);
  }
  const groupId = await chrome.tabs.group({ tabIds: [tabId] });
  await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: "blue" });
  return { groupId, windowId: tab.windowId };
}

/**
 * 拿到专属组，必要时重建并顺手做**重启认领**：按登记表扫全浏览器现存 tab，
 * origin+path 对上的收进组（`tabs.group` 会把别的窗口的 tab 搬过来）。组没丢
 * （SW 睡醒、session 还在）就跳过认领——那是重启场景才需要的重活。
 */
export async function ensureOwn(seedTabId?: number): Promise<OwnGroup> {
  if (cached !== null) {
    const alive = await chrome.tabGroups.get(cached.groupId).catch(() => null);
    if (alive !== null && alive.windowId === cached.windowId) {
      return cached;
    }
    cached = null;
  }
  if (building === null) {
    building = (async () => {
      // SW 刚睡醒：session 里记着的组还活着就直接认回来（不认领，重启才需要）。
      try {
        const got = (await chrome.storage.session.get(SESSION_KEY)) as Record<string, unknown>;
        const remembered = got[SESSION_KEY] as { groupId?: unknown } | undefined;
        if (remembered !== undefined && typeof remembered.groupId === "number") {
          const group = await chrome.tabGroups.get(remembered.groupId);
          return remember({ groupId: group.id, windowId: group.windowId });
        }
      } catch {
        // 记着的组没了（用户关组/关窗口）或 session 空：走重建。
      }
      const own = await findOrCreate(seedTabId);
      await reclaim(own.groupId);
      return remember(own);
    })().finally(() => {
      building = null; // 成败都清：失败不缓存，下一条命令重试拿真错误
    });
  }
  return building;
}

/** 只看不建：列表类命令（getTree）用它，免得列个 tab 还弹出一个新窗口。
 * session 没记（SW 刚醒/浏览器刚重启）就按标题只读找一次——Chrome 恢复会话时
 * 会把组原样还原，找到就当自己的用，不触发重建。 */
export async function peekOwnGroupId(): Promise<number | null> {
  try {
    const got = (await chrome.storage.session.get(SESSION_KEY)) as Record<string, unknown>;
    const remembered = got[SESSION_KEY] as { groupId?: unknown } | undefined;
    if (remembered !== undefined && typeof remembered.groupId === "number") {
      const group = await chrome.tabGroups.get(remembered.groupId);
      return group.id;
    }
  } catch {
    // 没记或组没了：往下按标题找。
  }
  try {
    const found = await chrome.tabGroups.query({ title: GROUP_TITLE });
    return found[0]?.id ?? null;
  } catch {
    return null;
  }
}

async function reclaim(groupId: number): Promise<void> {
  const owned = await registrySet();
  if (owned.size === 0) {
    return;
  }
  const tabs = await chrome.tabs.query({});
  const hits = tabs.filter((tab) => {
    const c = canonical(tab.url);
    return tab.id !== undefined && c !== null && owned.has(c);
  }).map((tab) => tab.id as number);
  // 特殊页（chrome:// 等）进不了组；逐个收，单个失败不拖垮整批。
  for (const tabId of hits) {
    await chrome.tabs.group({ tabIds: [tabId], groupId }).catch(() => undefined);
  }
}

/**
 * 归属判定的唯一入口（dispatch 的 choke point 调）：目标 tab 不在自己的组里
 * 就是拒绝，错误消息指向两条合法入口。
 */
export async function enforceOwnTab(tabId: number): Promise<void> {
  const { groupId } = await ensureOwn();
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab === null || tab.groupId !== groupId) {
    throw new CommandError(
      "no such frame",
      `not your page (${tab?.url ?? `tab ${tabId}`}): open it with \`browse tab open\` or adopt it with \`browse tab adopt\``,
    );
  }
  await recordVisit(tab.url);
}
