import { CommandError, queriesHang, querySettle } from "../protocol.ts";

/**
 * 页面归属（票 02 设计；2026-09-28 二改：多组）。
 *
 * 规则：**组成员身份 = 归属真源**，组名形如 `browse/<用途>`（CLI 的
 * `browse open --group <name>` / `browse group` 体系负责建组命名，用完
 * `browse group dissolve` 回收）。在任意 `browse/*` 组里的标签页就是
 * 「自己的页面」，页面方法只许落在它上面；拖进组即接管、拖出即放走。
 *
 * 判定懒做：每条命令现场查 `tab.groupId` 再对照现存 `browse/*` 组——
 * `chrome.tabGroups` 没有成员变更事件，监听拖拽不可靠，懒判零监听永不漂移。
 *
 * `chrome.storage.local["browse:ownership"]` = `{u: origin+path, g: 组名}` 数组
 * （LRU 上限 50），唯一用途是浏览器重启后按 URL 把现存 tab 认领回对应组。
 */

const GROUP_PREFIX = "browse/";
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

interface Owned {
  /** origin+path */
  u: string;
  /** 组名（不含 browse/ 前缀），认领回哪个组。 */
  g: string;
}

async function loadRegistry(): Promise<Owned[]> {
  const got = (await chrome.storage.local.get(REGISTRY_KEY)) as Record<string, unknown>;
  const raw = got[REGISTRY_KEY];
  return Array.isArray(raw)
    ? raw.filter((e): e is Owned =>
        typeof e === "object" && e !== null && typeof (e as Owned).u === "string"
          && typeof (e as Owned).g === "string")
    : [];
}

export async function registrySet(): Promise<Set<string>> {
  return new Set((await loadRegistry()).map((e) => e.u));
}

/**
 * 记一页「是我们开的/收编的」。LRU 前插；表头同页同组就不写。写失败静默——
 * 登记表只服务重启认领，坏一条不该炸命令。
 */
export async function recordVisit(url: string | null | undefined, group: string): Promise<void> {
  const c = canonical(url);
  if (c === null) {
    return;
  }
  try {
    const urls = await loadRegistry();
    if (urls[0]?.u === c && urls[0]?.g === group) {
      return;
    }
    const next: Owned[] = [{ u: c, g: group }, ...urls.filter((e) => e.u !== c)].slice(0, REGISTRY_CAP);
    await chrome.storage.local.set({ [REGISTRY_KEY]: next });
  } catch {
    // 存储坏了：认领会退化，权限判定不受影响（那是组前缀的事）。
  }
}

// ---------------------------------------------------------------- 归属判定

/**
 * 降级归属（Arc：query 全挂，组员身份判不了）：登记表 canonical(url) 命中即
 * 自己的，组名取登记值。「拖出组即撤权」在此退化为「登记表没有 = 不归属」；
 * URL 变了（origin+path 对不上）同样失效。
 */
async function registryGroupNameOf(tab: chrome.tabs.Tab): Promise<string | null> {
  const c = canonical(tab.url);
  if (c === null) {
    return null;
  }
  return (await loadRegistry()).find((e) => e.u === c)?.g ?? null;
}

/**
 * 现存所有 `browse/*` 组的 id。带 2 秒 TTL 缓存（2026-10-18，console 采集降 IPC）：
 * 组员身份是归属真源，撤销归属最迟 2 秒生效，与懒判语义兼容。
 */
let groupIdsCache: { ids: Set<number>; at: number } | null = null;
const GROUP_IDS_TTL = 2_000;

export async function ownGroupIds(): Promise<Set<number>> {
  if (groupIdsCache !== null && Date.now() - groupIdsCache.at < GROUP_IDS_TTL) {
    return groupIdsCache.ids;
  }
  const groups = await querySettle(chrome.tabGroups.query({}), "chrome.tabGroups.query").catch(() => []);
  const ids = new Set(groups.filter((g) => g.title?.startsWith(GROUP_PREFIX)).map((g) => g.id));
  groupIdsCache = { ids, at: Date.now() };
  return ids;
}

/** 一个 tab 是不是自己的页面：在任意 `browse/*` 组里；query 挂死降级为登记表命中。 */
export async function isOwnTab(tab: chrome.tabs.Tab): Promise<boolean> {
  if (await queriesHang()) {
    return (await registryGroupNameOf(tab)) !== null;
  }
  return tab.groupId !== undefined && (await ownGroupIds()).has(tab.groupId);
}

/**
 * own scope 的 tab 过滤（resolveContext / getTree 共用）：正常按组员身份，
 * query 挂死降级为登记表 canonical(url) 匹配。
 */
export async function filterOwnTabs(tabs: chrome.tabs.Tab[]): Promise<chrome.tabs.Tab[]> {
  if (await queriesHang()) {
    const reg = new Set((await loadRegistry()).map((e) => e.u));
    return tabs.filter((t) => {
      const c = canonical(t.url);
      return c !== null && reg.has(c);
    });
  }
  const groups = await ownGroupIds();
  return tabs.filter((tab) => tab.groupId !== undefined && groups.has(tab.groupId));
}

/** tab 所在组的组名（去 browse/ 前缀）；不在组里或不是自己的组返回 null。 */
async function ownGroupNameOf(tab: chrome.tabs.Tab): Promise<string | null> {
  if (await queriesHang()) {
    return registryGroupNameOf(tab);
  }
  if (tab.groupId === undefined) {
    return null;
  }
  const group = await chrome.tabGroups.get(tab.groupId).catch(() => null);
  const title = group?.title ?? "";
  return title.startsWith(GROUP_PREFIX) ? title.slice(GROUP_PREFIX.length) : null;
}

/**
 * 归属判定的唯一入口（dispatch 的 choke point 调）：目标 tab 不在任何
 * `browse/*` 组里就是拒绝，错误消息指向两条合法入口。
 */
export async function enforceOwnTab(tabId: number): Promise<void> {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const group = tab === null ? null : await ownGroupNameOf(tab);
  if (tab === null || group === null) {
    throw new CommandError(
      "no such frame",
      `not your page (${tab?.url ?? `tab ${tabId}`}): open it with \`browse open <url> --group <name>\` or adopt it with \`browse tab adopt <target> --group <name>\``,
    );
  }
  await recordVisit(tab.url, group);
}

/**
 * 重启认领：按登记表扫全浏览器现存 tab，origin+path 对上的收进它登记时的组
 * （组没了就在 tab 自己的窗口重建 `browse/<g>`）。特殊页（chrome:// 等）进不了
 * 组，逐个收，单个失败不拖垮整批。
 */
export async function reclaim(): Promise<void> {
  // 降级（Arc）：query 挂死，认领不回组也建不了组——登记表匹配就是全部归属，跳过。
  if (await queriesHang()) {
    return;
  }
  const registry = await loadRegistry();
  if (registry.length === 0) {
    return;
  }
  const tabs = await querySettle(chrome.tabs.query({}), "chrome.tabs.query");
  const byGroup = new Map<string, number[]>();
  for (const tab of tabs) {
    const hit = registry.find((e) => e.u === canonical(tab.url));
    if (hit !== undefined && tab.id !== undefined) {
      byGroup.set(hit.g, [...(byGroup.get(hit.g) ?? []), tab.id]);
    }
  }
  // 用完回收（用户 2026-09-28）：认领顺带清死条目——登记的 URL 已无活 tab
  // （页面被关/组被 dissolve）就从登记表丢掉，下次重启不再认领回来。
  const live = new Set(tabs.flatMap((tab) => {
    const c = canonical(tab.url);
    return c === null ? [] : [c];
  }));
  const kept = registry.filter((e) => live.has(e.u));
  if (kept.length !== registry.length) {
    await chrome.storage.local.set({ [REGISTRY_KEY]: kept });
  }
  for (const [name, tabIds] of byGroup) {
    // 单个失败不拖垮整批：建组本身也会挂（特殊页/窗口状态），包住整组
    try {
      const full = GROUP_PREFIX + name;
      const found = (await querySettle(chrome.tabGroups.query({ title: full }), "chrome.tabGroups.query"))[0];
      const groupId = found !== undefined
        ? found.id
        : await chrome.tabs.group({ tabIds: [tabIds[0]!] })
            .then((id) => chrome.tabGroups.update(id, { title: full, color: "blue" }).then(() => id));
      for (const tabId of tabIds) {
        await chrome.tabs.group({ tabIds: [tabId], groupId }).catch(() => undefined);
      }
    } catch {
      // 该组建不起来：跳过，下一组照常认领
    }
  }
}

/** 测试钩子：保留导出兼容旧名（多组后无缓存可清）。 */
export function resetOwnership(): void {
  reclaimed = false;
  groupIdsCache = null; // TTL 缓存跟着清，用例间不串世界
}

let reclaimed = false;

/** 重启认领每 SW 生命周期只跑一次（dispatch 的归属闸门调）。 */
export async function ensureReclaimed(): Promise<void> {
  if (reclaimed) {
    return;
  }
  reclaimed = true;
  await reclaim();
}
