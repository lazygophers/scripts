import { locateInPage } from "../src/locator.ts";

// The browser-neutral halves of the scaffolding live in the shared layer; they
// are re-exported here so every test keeps importing one file.
import {
  clearChrome,
  installChrome,
  page,
  rejectsWith,
  storageMock,
} from "../../shared/test/mock.ts";

export { clearChrome, installChrome, page, rejectsWith, storageMock };

type Any = Record<string, unknown>;

/**
 * `chrome.scripting` that really runs the injected function in this process,
 * so the page-side code under test is the code that ships. `files:` stands in
 * for page-locate.js by putting the real locator on `globalThis`.
 */
export function scriptingMock(): {
  executeScript: (injection: Any) => Promise<{ result: unknown }[]>;
  calls: Any[];
} {
  const calls: Any[] = [];
  return {
    calls,
    executeScript: async (injection: Any) => {
      calls.push(injection);
      if (Array.isArray(injection.files)) {
        (globalThis as Any).__browseLocate = locateInPage;
        return [{ result: undefined }];
      }
      const func = injection.func as (...args: unknown[]) => unknown;
      const args = (injection.args as unknown[]) ?? [];
      return [{ result: await func(...args) }];
    },
  };
}

type Tab = { id: number; url?: string; groupId?: number; active?: boolean; windowId?: number };

/**
 * 归属规则（票 02）之后，resolveContext / handlers 都要先有自己的专属组。这个
 * 助手装一个现成的世界：组 500 在窗口 20，传进来的 tabs 带上 `groupId: 500`
 * 就是「自己的页面」。返回的 `store.data["browse:ownership"]` 是登记表，可断言。
 */
export function ownWorld(
  tabs: Tab[],
  opts: { registry?: string[]; local?: Record<string, unknown>; extra?: Record<string, unknown> } = {},
): {
  group: Record<string, unknown>;
  session: Record<string, unknown>;
  store: Record<string, unknown>;
  tabs: Tab[];
} {
  const group: Record<string, unknown> = {
    id: 500, windowId: 20, title: "browse/default", color: "blue", collapsed: false,
  };
  const session: Record<string, unknown> = {};
  // 确认模式默认 always：确认钩子必须被问到。老世界里 storage 缺席 = 最严模式，
  // 有了 local 空对象反而变 silent，钩子永远不被调——测试会静默失去覆盖。
  const store: Record<string, unknown> = {
    "browse:config": { confirm_mode: "always" },
    ...(opts.local ?? {}),
    "browse:ownership": (opts.registry ?? []).map((u) =>
      typeof u === "string" ? { u, g: "default" } : u),
  };
  const all = tabs.map((t) => ({ windowId: 20, ...t }));
  installChrome({
    tabs: {
      query: async () => all,
      get: async (id: number) => {
        const tab = all.find((t) => t.id === id);
        if (tab === undefined) throw new Error(`no tab ${id}`);
        return tab;
      },
      group: async ({ tabIds, groupId }: { tabIds: number[]; groupId?: number }) => {
        for (const id of tabIds) {
          const tab = all.find((t) => t.id === id);
          if (tab !== undefined) tab.groupId = groupId ?? 500;
        }
        return groupId ?? 500;
      },
    },
    tabGroups: {
      get: async (id: number) => {
        if (id !== group.id) throw new Error(`no group ${id}`);
        return group;
      },
      query: async (q: { title?: string }) =>
        (q?.title === undefined || q.title === group.title) ? [group] : [],
      update: async (id: number, delta: Record<string, unknown>) => {
        if (id !== group.id) throw new Error(`no group ${id}`);
        return Object.assign(group, delta);
      },
    },
    windows: {
      create: async () => ({ id: 21, tabs: [{ id: 901, windowId: 21 }] }),
    },
    storage: {
      local: {
        get: async (key: string) => (key in store ? { [key]: store[key] } : {}),
        set: async (items: Record<string, unknown>) => void Object.assign(store, items),
        remove: async (key: string) => void delete store[key],
      },
      session: {
        get: async (key: string) => (key in session ? { [key]: session[key] } : {}),
        set: async (items: Record<string, unknown>) => void Object.assign(session, items),
      },
    },
    ...(opts.extra ?? {}),
  });
  return { group, session, store, tabs: all };
}

/**
 * 归属规则的最低夹具件：session 里记着自己的组 500（窗口 20）。装了 tabs 夹具
 * 但不关心归属的用例直接展开它——resolveContext 的 own scope 总要先拿到组。
 * 夹具里的标签页要带 `groupId: 500` 才算自己的页面。
 */
export function ownSession(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tabGroups: {
      get: async (id: number) => {
        if (id !== 500) throw new Error(`no group ${id}`);
        return { id: 500, windowId: 20, title: "browse/default" };
      },
      query: async (q: { title?: string }) =>
        (q?.title === undefined || q.title === "browse/default")
          ? [{ id: 500, windowId: 20, title: "browse/default" }]
          : [],
      update: async () => ({ id: 500, title: "browse/default" }),
    },
    windows: { create: async () => ({ id: 21, tabs: [{ id: 901 }] }) },
    storage: {
      session: { get: async () => ({ "browse:own": { groupId: 500 } }), set: async () => {} },
      local: { get: async () => ({ "browse:config": { confirm_mode: "always" } }), set: async () => {} },
    },
    ...extra,
  };
}
