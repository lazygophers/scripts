import assert from "node:assert/strict";
import test from "node:test";
import {
  tabsAdopt,
  tabsGroup,
  tabsGroups,
  tabsUngroup,
  tabsUpdateGroup,
} from "../src/handlers/tabs.ts";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import { clearContextCache } from "../src/handlers/context.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

/**
 * 夹具：一个可变浏览器世界。组 500 = browse/default（自己的），组 501 = 别的
 * browse/other，组 9 = 普通组（非 browse/）。tab 7 在组 500（own），tab 8 无组，
 * tab 9 在组 9。
 */
function setup() {
  const calls: Any = { grouped: [], ungrouped: [], updated: [], registrySets: [] };
  const groups = new Map<number, Any>([
    [500, { id: 500, windowId: 20, title: "browse/default", color: "blue", collapsed: false }],
    [501, { id: 501, windowId: 20, title: "browse/other", color: "grey", collapsed: true }],
    [9, { id: 9, windowId: 20, title: "work", color: "red", collapsed: false }],
  ]);
  const tabs = new Map<number, Any>([
    [7, { id: 7, windowId: 20, url: "https://own.test/a", groupId: 500, active: true }],
    [8, { id: 8, windowId: 20, url: "https://foreign.test/b", groupId: undefined }],
    [9, { id: 9, windowId: 20, url: "https://work.test/c", groupId: 9 }],
  ]);
  let nextGroupId = 600;
  const localData: Any = {
    "browse:config": { confirm_mode: "always" },
    "browse:ownership": [],
  };
  installChrome({
    tabs: {
      query: async (q: Any = {}) => {
        const all = [...tabs.values()];
        if (q.groupId !== undefined) return all.filter((t) => t.groupId === q.groupId);
        if (q.active) return all.filter((t) => t.active);
        return all;
      },
      get: async (id: number) => {
        const tab = tabs.get(id);
        if (tab === undefined) throw new Error(`no tab ${id}`);
        return tab;
      },
      group: async (opts: Any) => {
        calls.grouped.push(opts);
        let gid = opts.groupId;
        if (gid === undefined) {
          gid = nextGroupId++;
          groups.set(gid, { id: gid, windowId: 20, title: "", color: "grey", collapsed: false });
        }
        for (const id of opts.tabIds as number[]) {
          tabs.get(id).groupId = gid;
        }
        return gid;
      },
      ungroup: async (ids: number | number[]) => {
        const list = Array.isArray(ids) ? ids : [ids];
        calls.ungrouped.push(list);
        for (const id of list) tabs.get(id).groupId = undefined;
      },
    },
    tabGroups: {
      get: async (id: number) => {
        const g = groups.get(id);
        if (g === undefined) throw new Error(`no group ${id}`);
        return g;
      },
      query: async (q: Any = {}) =>
        [...groups.values()].filter(
          (g) => (q.title === undefined || g.title === q.title)
            && (q.color === undefined || g.color === q.color)),
      update: async (id: number, changes: Any) => {
        calls.updated.push([id, changes]);
        const g = groups.get(id);
        if (g === undefined) throw new Error(`no group ${id}`);
        Object.assign(g, changes);
        return g;
      },
    },
    windows: { getCurrent: async () => ({ id: 20 }) },
    storage: {
      session: {
        get: async () => ({ "browse:own": { groupId: 500 } }),
        set: async () => {},
      },
      local: {
        get: async (keys?: string | string[]) => {
          const out: Any = {};
          if (keys === undefined) return { ...localData };
          for (const k of Array.isArray(keys) ? keys : [keys]) out[k] = localData[k];
          return out;
        },
        set: async (items: Any) => {
          Object.assign(localData, items);
          calls.registrySets.push(items);
        },
      },
    },
  });
  clearContextCache();
  return { calls, groups, tabs, localData };
}

test.beforeEach(() => {
  setConfirmHook(async () => true);
  clearContextCache();
});

test.afterEach(() => {
  setConfirmHook(null as unknown as () => Promise<void>);
  clearChrome();
});

test("group: new group echoes tabGroups.get, update path sets title/color", async () => {
  const { calls } = setup();
  // 不给 group 参数：Chrome 语义是新建一组（哪怕 tab 已在组里）
  assert.deepEqual(
    await tabsGroup({ context: "7" }),
    { group: "600", title: "", color: "grey" },
  );
  assert.deepEqual(calls.grouped, [{ tabIds: [7] }]);

  assert.deepEqual(
    await tabsGroup({ context: "7", title: "browse/x", color: "red", group: "501" }),
    { group: "501", title: "browse/x", color: "red" },
  );
  assert.deepEqual(calls.updated.at(-1), [501, { title: "browse/x", color: "red" }]);
});

test("group rejects bad color, bad group id and frame contexts", async () => {
  setup();
  await rejectsWith(() => tabsGroup({ context: "7", color: "chartreuse" }), "invalid argument");
  await rejectsWith(() => tabsGroup({ context: "7", group: "abc" }), "invalid argument");
  await rejectsWith(() => tabsGroup({ context: "7.0" }), "unsupported operation");
});

test("ungroup: by context, by group id, empty group errors", async () => {
  const { calls } = setup();
  assert.deepEqual(await tabsUngroup({ context: "7" }), { ungrouped: 1 });
  assert.deepEqual(calls.ungrouped.at(-1), [7]);

  assert.deepEqual(await tabsUngroup({ group: "9" }), { ungrouped: 1 });
  await rejectsWith(() => tabsUngroup({ group: "604" }), "invalid argument");
  await rejectsWith(() => tabsUngroup({ group: "x" }), "invalid argument");
});

test("groups: lists with tab ids, filters by title and color", async () => {
  setup();
  const all = await tabsGroups({});
  assert.deepEqual(
    all.groups.map((g) => g.group),
    ["500", "501", "9"],
  );
  assert.deepEqual(all.groups[0], {
    group: "500", title: "browse/default", color: "blue", collapsed: false, window: 20, tabs: [7],
  });
  const only = await tabsGroups({ title: "browse/other" });
  assert.deepEqual(only.groups.map((g) => g.group), ["501"]);
  const red = await tabsGroups({ color: "red" });
  assert.deepEqual(red.groups.map((g) => g.group), ["9"]);
  await rejectsWith(() => tabsGroups({ color: "teal" }), "invalid argument");
});

test("updateGroup: happy path and every validation branch", async () => {
  const { calls } = setup();
  assert.deepEqual(
    await tabsUpdateGroup({ group: "500", title: "browse/new" }),
    { group: "500", title: "browse/new", color: "blue", collapsed: false },
  );
  assert.deepEqual(calls.updated.at(-1), [500, { title: "browse/new" }]);

  await rejectsWith(() => tabsUpdateGroup({ title: "x" }), "invalid argument");
  await rejectsWith(() => tabsUpdateGroup({ group: "500", color: "nope" }), "invalid argument");
  await rejectsWith(() => tabsUpdateGroup({ group: "500", collapsed: "yes" }), "invalid argument");
  await rejectsWith(() => tabsUpdateGroup({ group: "500" }), "invalid argument");
});

test("adopt: needs matchUrl or context, rejects frames, records the visit", async () => {
  const { calls, localData } = setup();
  await rejectsWith(() => tabsAdopt({}), "invalid argument");
  await rejectsWith(() => tabsAdopt({ context: "8.0" }), "unsupported operation");

  const asked: ConfirmRequest[] = [];
  setConfirmHook(async (req) => {
    asked.push(req);
    return true;
  });
  const result = await tabsAdopt({ context: "8", group: "other" });
  assert.deepEqual(result, { context: "8", group: "other", url: "https://foreign.test/b" });
  assert.equal(asked.length, 1);
  assert.equal(asked[0].action, "adoptTab");
  // browse/other 组已存在（501），收编进它
  assert.deepEqual(calls.grouped.at(-1), { tabIds: [8], groupId: 501 });
  const reg = localData["browse:ownership"] as Any[];
  assert.equal(reg[0].u, "https://foreign.test/b");
  assert.equal(reg[0].g, "other");
});

test("adopt: creates the group when missing, idempotent when already own", async () => {
  const { calls } = setup();
  const result = await tabsAdopt({ context: "9" });
  assert.deepEqual(result, { context: "9", group: "default", url: "https://work.test/c" });
  // 无现成 browse/default？有（500）——tab 9 应进 500
  assert.deepEqual(calls.grouped.at(-1), { tabIds: [9], groupId: 500 });

  // 已是自己的 tab：幂等成功，不弹确认、不动组
  setConfirmHook(async () => {
    throw new Error("must not confirm an own tab");
  });
  assert.deepEqual(
    await tabsAdopt({ context: "7", group: "whatever" }),
    { context: "7", group: "whatever", url: "https://own.test/a" },
  );
});

test("adopt: builds a fresh group when no browse/ group matches the name", async () => {
  const { calls } = setup();
  await tabsAdopt({ context: "8", group: "fresh" });
  // browse/fresh 不存在 → tabs.group 新建 + tabGroups.update 改名
  const last = calls.grouped.at(-1) as Any;
  assert.deepEqual(last.tabIds, [8]);
  assert.equal(last.groupId, undefined);
});
