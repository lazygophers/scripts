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
          ...(color === undefined ? {} : { color: color as chrome.tabGroups.ColorEnum }),
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
    await chrome.tabs.ungroup(tabs.map((t) => t.id).filter((id): id is number => id !== undefined));
    return { ungrouped: tabs.length };
  }
  const target = await requireTabOnly(params);
  await chrome.tabs.ungroup(target.tabId);
  return { ungrouped: 1 };
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
