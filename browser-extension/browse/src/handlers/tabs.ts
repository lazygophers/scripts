import { CommandError, optionalString } from "../protocol.ts";
import { requireApi, resolveContext, type Target } from "./context.ts";

/** `chrome.tabGroups` 接受的颜色是固定的一套，不是任意 CSS 色。 */
const COLORS = new Set(["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]);

function groupIdOf(raw: string): number {
  if (!/^\d+$/.test(raw)) {
    throw new CommandError("invalid argument", `group must be a group id, got ${raw}`);
  }
  return Number(raw);
}

/** `lg:tabs.group`（私有方法）：把标签页放进分组。不传 `group` 就新建一组。 */
export async function tabsGroup(
  params: Record<string, unknown>,
): Promise<{ group: string; title: string; color: string }> {
  requireApi("tabs.group", "grouping tabs");
  const target = await requireTabOnly(params);
  const title = optionalString(params.title, "title");
  const color = optionalString(params.color, "color");
  if (color !== undefined && !COLORS.has(color)) {
    throw new CommandError("invalid argument", `color must be one of ${[...COLORS].join(", ")}`);
  }
  const group = optionalString(params.group, "group", ", a group id from lg:tabs.group");
  const groupId = await chrome.tabs.group({
    tabIds: [target.tabId],
    ...(group === undefined ? {} : { groupId: groupIdOf(group) }),
  });
  // 回显分组状态；title/color 至少一个时顺手改掉。tabGroups.* 需要 manifest 里
  // 单独的 "tabGroups" 权限，tabs.group/ungroup 不需要。
  requireApi("tabGroups", "reading or setting the group's title and colour");
  const after =
    title === undefined && color === undefined
      ? await chrome.tabGroups.get(groupId)
      : await chrome.tabGroups.update(groupId, {
          ...(title === undefined ? {} : { title }),
          // COLORS 校验过了，这里只是把 string 收窄回 Chrome 的枚举类型
          ...(color === undefined ? {} : { color: color as `${chrome.tabGroups.Color}` }),
        });
  return { group: String(groupId), title: after?.title ?? "", color: after?.color ?? "" };
}

/**
 * `lg:tabs.ungroup`（私有方法）：`context` 拆一个标签页；`group` 拆散整组
 * （Chrome 的组空了就自动消失，所以「删组」就是全拆）。
 */
export async function tabsUngroup(
  params: Record<string, unknown>,
): Promise<{ ungrouped: number }> {
  requireApi("tabs.ungroup", "ungrouping tabs");
  const group = optionalString(params.group, "group", ", a group id from lg:tabs.group");
  if (group !== undefined) {
    const tabs = await chrome.tabs.query({ groupId: groupIdOf(group) });
    if (tabs.length === 0) {
      throw new CommandError("invalid argument", `no such group ${group}`);
    }
    const ids = tabs.map((t) => t.id).filter((id): id is number => id !== undefined);
    await chrome.tabs.ungroup(ids as [number, ...number[]]);
    return { ungrouped: tabs.length };
  }
  const target = await requireTabOnly(params);
  await chrome.tabs.ungroup(target.tabId);
  return { ungrouped: 1 };
}

/**
 * `lg:tabs.groups`（私有方法）：列出标签组。可选按标题/颜色过滤，每组的 tab
 * 清单一并带回（CLI 不用再逐个查）。
 */
export async function tabsGroups(
  params: Record<string, unknown>,
): Promise<{ groups: { group: string; title: string; color: string; collapsed: boolean; window: number; tabs: number[] }[] }> {
  requireApi("tabGroups", "listing tab groups");
  const title = optionalString(params.title, "title");
  const color = optionalString(params.color, "color");
  if (color !== undefined && !COLORS.has(color)) {
    throw new CommandError("invalid argument", `color must be one of ${[...COLORS].join(", ")}`);
  }
  const found = await chrome.tabGroups.query({
    ...(title === undefined ? {} : { title }),
    ...(color === undefined ? {} : { color: color as `${chrome.tabGroups.Color}` }),
  });
  const tabs = await chrome.tabs.query({});
  return {
    groups: found.map((group) => ({
      group: String(group.id),
      title: group.title ?? "",
      color: group.color,
      collapsed: group.collapsed,
      window: group.windowId,
      tabs: tabs
        .filter((tab) => tab.groupId === group.id)
        .map((tab) => tab.id)
        .filter((id): id is number => id !== undefined),
    })),
  };
}

/**
 * `lg:tabs.updateGroup`（私有方法）：改组的标题/颜色/折叠态。
 */
export async function tabsUpdateGroup(
  params: Record<string, unknown>,
): Promise<{ group: string; title: string; color: string; collapsed: boolean }> {
  requireApi("tabGroups", "updating a tab group");
  const group = optionalString(params.group, "group", ", a group id from lg:tabs.groups");
  if (group === undefined || !/^\d+$/.test(group)) {
    throw new CommandError("invalid argument", `group must be a group id, got ${group}`);
  }
  const title = optionalString(params.title, "title");
  const color = optionalString(params.color, "color");
  if (color !== undefined && !COLORS.has(color)) {
    throw new CommandError("invalid argument", `color must be one of ${[...COLORS].join(", ")}`);
  }
  const collapsed = params.collapsed;
  if (collapsed !== undefined && typeof collapsed !== "boolean") {
    throw new CommandError("invalid argument", "collapsed must be true or false");
  }
  if (title === undefined && color === undefined && collapsed === undefined) {
    throw new CommandError("invalid argument", "give at least one of title / color / collapsed");
  }
  const updated = await chrome.tabGroups.update(Number(group), {
    ...(title === undefined ? {} : { title }),
    ...(color === undefined ? {} : { color: color as `${chrome.tabGroups.Color}` }),
    ...(collapsed === undefined ? {} : { collapsed }),
  });
  // 新版类型把返回值标成可能没有：真取不到就把请求里那几个值原样报回去。
  return {
    group: String(updated?.id ?? group),
    title: updated?.title ?? title ?? "",
    color: updated?.color ?? color ?? "",
    collapsed: updated?.collapsed ?? collapsed ?? false,
  };
}

/** group works on a tab; a frame id is rejected, same rule as close/activate. */
async function requireTabOnly(params: Record<string, unknown>): Promise<Target> {
  const target = await resolveContext(params);
  if (target.frameId !== undefined) {
    throw new CommandError(
      "unsupported operation",
      `grouping works on a tab, not a frame; drop the .${target.frameId} suffix`,
    );
  }
  return target;
}
