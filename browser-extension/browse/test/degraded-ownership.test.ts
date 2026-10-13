import assert from "node:assert/strict";
import test from "node:test";
import { resetQueryProbe, seedQueryProbe } from "../src/protocol.ts";
import { enforceOwnTab, isOwnTab, resetOwnership } from "../src/handlers/ownership.ts";
import { tabsGroup, tabsGroups } from "../src/handlers/tabs.ts";
import { clearChrome, ownWorld, rejectsWith } from "./mock.ts";

// Arc 无组降级（2026-10-13）：query 挂死时归属退回登记表，成组退回只登记。
test.afterEach(() => {
  resetOwnership();
  resetQueryProbe();
  clearChrome();
});

test("degraded ownership: registry canonical match owns, no entry refuses", async () => {
  seedQueryProbe(true);
  const { tabs } = ownWorld([
    { id: 7, url: "https://a.test/p?x=1" }, // 查询串不影响匹配
    { id: 8, url: "https://b.test/" },
  ], { registry: [{ u: "https://a.test/p", g: "proj" }] });
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 7)!), true);
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 8)!), false, "登记表没有 = 不归属");
  await enforceOwnTab(7); // 不抛
  const err = await rejectsWith(() => enforceOwnTab(8), "no such frame");
  assert.match(err.message, /not your page/);
});

test("degraded ownership: URL change invalidates the registry entry", async () => {
  seedQueryProbe(true);
  const { tabs } = ownWorld(
    [{ id: 7, url: "https://a.test/other" }],
    { registry: [{ u: "https://a.test/p", g: "proj" }] },
  );
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 7)!), false, "URL 变了即失效");
});

test("degraded resolveContext own scope: registry filter drives matchUrl and active fallback", async () => {
  seedQueryProbe(true);
  ownWorld(
    [{ id: 7, url: "https://a.test/p", active: true }, { id: 8, url: "https://b.test/" }],
    { registry: [{ u: "https://a.test/p", g: "proj" }] },
  );
  const { resolveContext } = await import("../src/handlers/context.ts");
  const hit = await resolveContext({ matchUrl: "*a.test*" });
  assert.equal(hit.tabId, 7);
  await rejectsWith(() => resolveContext({ matchUrl: "*b.test*" }), "no such frame");
});

test("degraded tabsGroup: no real group, writes the registry, marks lg:degraded", async () => {
  seedQueryProbe(true);
  const { tabs, store } = ownWorld([{ id: 5, url: "https://a.test/x" }]);
  const r = await tabsGroup({ context: "5", title: "browse/proj", color: "blue" });
  assert.equal(r["lg:degraded"], true);
  assert.equal(r.title, "browse/proj", "CLI 回显组名靠 title");
  assert.equal(tabs.find((t) => t.id === 5)?.groupId, undefined, "没成真组");
  assert.deepEqual(store["browse:ownership"]?.[0], { u: "https://a.test/x", g: "proj" });
});

test("degraded tabsGroups: empty list with the marker, so CLI open proceeds", async () => {
  seedQueryProbe(true);
  ownWorld([]);
  const r = await tabsGroups({});
  assert.deepEqual(r.groups, []);
  assert.equal(r["lg:degraded"], true);
});

test("probe not seeded: normal Chrome path still groups for real", async () => {
  const { tabs } = ownWorld([{ id: 5, url: "https://a.test/x" }]);
  const r = await tabsGroup({ context: "5", title: "browse/proj" });
  assert.equal(r["lg:degraded"], undefined);
  assert.equal(r.group, "500", "真成组：回显组 id");
  assert.equal(tabs.find((t) => t.id === 5)?.groupId, 500);
});
