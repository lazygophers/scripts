import { CommandError } from "../protocol.js";

interface ContextInfo {
  context: string;
  parent: string | null;
  url: string;
  children: ContextInfo[];
  /** Not in BiDi: the tab title, which every caller of this wants anyway. */
  "lg:title"?: string;
  "lg:active"?: boolean;
}

/**
 * BiDi `browsingContext.getTree`. Top-level contexts are tabs; children are
 * frames from `webNavigation.getAllFrames`. Context ids are `<tabId>` for a
 * tab and `<tabId>.<frameId>` for a frame, so any later command can address a
 * frame without a second lookup.
 */
export async function browsingContextGetTree(
  params: Record<string, unknown>,
): Promise<{ contexts: ContextInfo[] }> {
  const root = params.root;
  if (root !== undefined && typeof root !== "string") {
    throw new CommandError("invalid argument", "root must be a context id string");
  }

  const tabs = await chrome.tabs.query({});
  const wanted = root === undefined ? tabs : tabs.filter((t) => String(t.id) === root);
  if (root !== undefined && wanted.length === 0) {
    throw new CommandError("no such frame", `no browsing context ${root}`);
  }

  const contexts = await Promise.all(wanted.map(tabToContext));
  return { contexts };
}

async function tabToContext(tab: chrome.tabs.Tab): Promise<ContextInfo> {
  const tabId = tab.id ?? -1;
  return {
    context: String(tabId),
    parent: null,
    url: tab.url ?? "",
    "lg:title": tab.title ?? "",
    "lg:active": tab.active,
    children: await frameChildren(tabId),
  };
}

async function frameChildren(tabId: number): Promise<ContextInfo[]> {
  if (tabId < 0) {
    return [];
  }
  // Chrome refuses this on chrome:// and similar; a tab with no readable
  // frames is still a valid context, so report it with no children.
  const frames = await chrome.webNavigation.getAllFrames({ tabId }).catch(() => null);
  if (!frames) {
    return [];
  }
  return frames
    .filter((f) => f.frameId !== 0)
    .map((f) => ({
      context: `${tabId}.${f.frameId}`,
      parent: f.parentFrameId === 0 ? String(tabId) : `${tabId}.${f.parentFrameId}`,
      url: f.url,
      children: [],
    }));
}
