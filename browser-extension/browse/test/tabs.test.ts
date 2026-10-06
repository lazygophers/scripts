import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  tabsAdopt,
  tabsGroup,
  tabsGroups,
  tabsUngroup,
  tabsUpdateGroup,
} from "../src/handlers/tabs.ts";
import { clearContextCache } from "../src/handlers/context.ts";
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

afterEach(() => {
  clearChrome();
  clearContextCache();
});

describe("tabs.group", () => {
  it("creates a new group when none is given", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabs: { group: async () => 777, ungroup: async () => {} },
      tabGroups: {
        get: async (id: number) => ({ id, title: "browse/default", color: "blue" }),
        update: async (id: number, d: Any) => ({ id, title: d.title, color: d.color }),
        query: async () => [],
      },
    });
    const r = await tabsGroup({ context: "1" });
    assert.deepEqual(r, { group: "777", title: "browse/default", color: "blue" });
  });

  it("updates title and colour of an existing group", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    const updates: Any[] = [];
    installChrome({
      tabs: { group: async ({ groupId }: Any) => groupId, ungroup: async () => {} },
      tabGroups: {
        get: async () => {
          throw new Error("should not get");
        },
        update: async (id: number, d: Any) => (updates.push(d), { id, ...d }),
        query: async () => [],
      },
    });
    const r = await tabsGroup({ context: "1", group: "500", title: "t", color: "red" });
    assert.deepEqual(r, { group: "500", title: "t", color: "red" });
    assert.deepEqual(updates[0], { title: "t", color: "red" });
  });

  it("rejects unknown colours, bad group ids and frame targets", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabs: { group: async () => 1, ungroup: async () => {} },
      tabGroups: { get: async () => ({}), update: async () => ({}), query: async () => [] },
    });
    await rejectsWith(() => tabsGroup({ context: "1", color: "chartreuse" }), "invalid argument");
    await rejectsWith(() => tabsGroup({ context: "1", group: "abc" }), "invalid argument");
    await rejectsWith(() => tabsGroup({ context: "1.2" }), "unsupported operation");
  });

  it("refuses when the browser cannot group tabs", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => tabsGroup({ context: "1" }), "unsupported operation");
  });
});

describe("tabs.ungroup", () => {
  it("dissolves a whole group by id", async () => {
    const w = ownWorld([
      { id: 1, url: "https://a.example/x", groupId: 500 },
      { id: 2, url: "https://b.example/y", groupId: 500 },
    ]);
    let ungrouped: number[] = [];
    installChrome({
      tabs: {
        query: async (q: Any) => (q.groupId === 500 ? w.tabs : []),
        ungroup: async (ids: number[]) => (ungrouped = ids),
      },
    });
    const r = await tabsUngroup({ group: "500" });
    assert.deepEqual(r, { ungrouped: 2 });
    assert.deepEqual(ungrouped, [1, 2]);
  });

  it("rejects an empty group and ungroups a single context otherwise", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    let ungrouped: unknown;
    installChrome({
      tabs: {
        query: async () => [],
        ungroup: async (ids: unknown) => (ungrouped = ids),
      },
    });
    await rejectsWith(() => tabsUngroup({ group: "500" }), "invalid argument");
    assert.deepEqual(await tabsUngroup({ context: "1" }), { ungrouped: 1 });
    assert.deepEqual(ungrouped, 1);
  });
});

describe("tabs.groups", () => {
  it("lists groups with their tab members", async () => {
    const w = ownWorld([
      { id: 1, url: "https://a.example/x", groupId: 500 },
      { id: 2, url: "https://b.example/y", groupId: 501 },
    ]);
    installChrome({
      tabGroups: {
        query: async () => [w.group, { ...w.group, id: 501, title: "browse/other", color: "red" }],
        update: async () => ({}),
        get: async () => w.group,
      },
      tabs: { query: async () => w.tabs, group: async () => 1, ungroup: async () => {} },
    });
    const r = await tabsGroups({});
    assert.equal(r.groups.length, 2);
    assert.deepEqual(r.groups[0], {
      group: "500",
      title: "browse/default",
      color: "blue",
      collapsed: false,
      window: 20,
      tabs: [1],
    });
    assert.deepEqual(r.groups[1]?.tabs, [2]);
  });

  it("filters by title and rejects unknown colours", async () => {
    const w = ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabGroups: {
        query: async (q: Any) => (q.title === "browse/default" ? [w.group] : []),
        update: async () => ({}),
        get: async () => w.group,
      },
      tabs: { query: async () => w.tabs, group: async () => 1, ungroup: async () => {} },
    });
    const r = await tabsGroups({ title: "browse/default" });
    assert.equal(r.groups.length, 1);
    await rejectsWith(() => tabsGroups({ color: "nope" }), "invalid argument");
  });
});

describe("tabs.updateGroup", () => {
  it("updates title, colour and collapsed state", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabGroups: {
        update: async (id: number, d: Any) => ({ id, title: "browse/default", color: "blue", ...d }),
        query: async () => [],
        get: async () => ({}),
      },
      tabs: { group: async () => 1, ungroup: async () => {} },
    });
    const r = await tabsUpdateGroup({ group: "500", title: "x", color: "pink", collapsed: true });
    assert.deepEqual(r, { group: "500", title: "x", color: "pink", collapsed: true });
  });

  it("validates its arguments", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabGroups: { update: async () => ({}), query: async () => [], get: async () => ({}) },
      tabs: { group: async () => 1, ungroup: async () => {} },
    });
    await rejectsWith(() => tabsUpdateGroup({}), "invalid argument");
    await rejectsWith(() => tabsUpdateGroup({ group: "x1" }), "invalid argument");
    await rejectsWith(() => tabsUpdateGroup({ group: "500", color: "nope" }), "invalid argument");
    await rejectsWith(() => tabsUpdateGroup({ group: "500", collapsed: "yes" }), "invalid argument");
    await rejectsWith(() => tabsUpdateGroup({ group: "500" }), "invalid argument");
  });

  it("falls back to the requested values when update returns nothing", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabGroups: { update: async () => undefined, query: async () => [], get: async () => ({}) },
      tabs: { group: async () => 1, ungroup: async () => {} },
    });
    const r = await tabsUpdateGroup({ group: "500", title: "t" });
    assert.deepEqual(r, { group: "500", title: "t", color: "", collapsed: false });
  });
});

describe("tabs.adopt", () => {
  it("needs matchUrl or context", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({ tabs: { group: async () => 1 } });
    await rejectsWith(() => tabsAdopt({}), "invalid argument");
  });

  it("is idempotent for a tab that is already ours", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }], {
      registry: ["https://a.example/x"],
    });
    const r = await tabsAdopt({ context: "1" });
    assert.deepEqual(r, { context: "1", group: "default", url: "https://a.example/x" });
  });

  it("adopts a foreign tab into an existing browse group", async () => {
    const w = ownWorld([
      { id: 1, url: "https://own.example/x", groupId: 500 },
      { id: 9, url: "https://foreign.example/y" },
    ]);
    installChrome({
      tabGroups: {
        query: async (q: Any) => (q.title === "browse/default" ? [w.group] : []),
        update: async () => ({}),
        get: async () => w.group,
      },
      tabs: {
        get: async (id: number) => w.tabs.find((t) => t.id === id)!,
        group: async ({ tabIds, groupId }: Any) => {
          for (const id of tabIds) {
            const tab = w.tabs.find((t) => t.id === id);
            if (tab !== undefined) tab.groupId = groupId;
          }
          return groupId;
        },
        ungroup: async () => {},
      },
    });
    const r = await tabsAdopt({ context: "9" });
    assert.deepEqual(r, { context: "9", group: "default", url: "https://foreign.example/y" });
    assert.equal(w.tabs.find((t) => t.id === 9)?.groupId, 500);
  });

  it("creates a new browse group when none exists yet", async () => {
    const w = ownWorld([
      { id: 1, url: "https://own.example/x", groupId: 500 },
      { id: 9, url: "https://foreign.example/y" },
    ]);
    installChrome({
      tabGroups: {
        query: async () => [],
        update: async (id: number, d: Any) => Object.assign({ id }, d),
        get: async () => w.group,
      },
      tabs: {
        get: async (id: number) => w.tabs.find((t) => t.id === id)!,
        group: async ({ tabIds, groupId }: Any) => {
          for (const id of tabIds) {
            const tab = w.tabs.find((t) => t.id === id);
            if (tab !== undefined) tab.groupId = groupId ?? 888;
          }
          return groupId ?? 888;
        },
        ungroup: async () => {},
      },
    });
    const r = await tabsAdopt({ context: "9", group: "work" });
    assert.equal(r.group, "work");
    assert.equal(w.tabs.find((t) => t.id === 9)?.groupId, 888);
  });

  it("refuses frame targets and missing APIs", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({ tabs: { group: async () => 1 }, tabGroups: { query: async () => [] } });
    await rejectsWith(() => tabsAdopt({ context: "1.2" }), "unsupported operation");
    installChrome({ runtime: {} });
    await rejectsWith(() => tabsAdopt({ context: "1" }), "unsupported operation");
  });
});

describe("tabs.group 字段组合", () => {
  it("updates title only, colour only, and tolerates a titleless group", async () => {
    const w = ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500 }]);
    installChrome({
      tabs: { query: async () => w.tabs, group: async ({ groupId }: Any) => groupId ?? 777, ungroup: async () => {} },
      tabGroups: {
        update: async (id: number, d: Any) => ({ id, ...d }),
        get: async () => w.group,
        query: async () => [{ ...w.group, title: undefined }],
      },
    });
    assert.deepEqual(await tabsGroup({ context: "1", title: "t" }), {
      group: "777", title: "t", color: "",
    });
    assert.deepEqual(await tabsGroup({ context: "1", color: "cyan" }), {
      group: "777", title: "", color: "cyan",
    });
    const listed = await tabsGroups({});
    assert.equal(listed.groups[0]?.title, "", "没有 title 的组显示空串");
  });

  it("adopt works on a tab without a url", async () => {
    ownWorld([{ id: 1, url: "https://own.example/x", groupId: 500 }, { id: 9 }]);
    installChrome({
      tabGroups: { query: async () => [], update: async () => ({}), get: async () => ({}) },
      tabs: { get: async (id: number) => ({ id }), group: async () => 1, ungroup: async () => {} },
    });
    const r = await tabsAdopt({ context: "9" });
    assert.equal(r.url, "");
  });
});
