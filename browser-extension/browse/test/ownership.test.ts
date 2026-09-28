import assert from "node:assert/strict";
import test from "node:test";
import { setConfirmHook } from "../src/handlers/confirm.ts";
import {
  canonical,
  enforceOwnTab,
  isOwnTab,
  recordVisit,
  registrySet,
  resetOwnership,
} from "../src/handlers/ownership.ts";
import { clearChrome, ownWorld, rejectsWith } from "./mock.ts";

test.afterEach(() => {
  resetOwnership();
  setConfirmHook(async () => true);
  clearChrome();
});

test("canonical drops the query string and the hash, keeps origin+path", () => {
  assert.equal(canonical("https://a.test/p?x=1#frag"), "https://a.test/p");
  assert.equal(canonical("https://a.test"), "https://a.test/");
  assert.equal(canonical("about:blank"), null, "不透明 origin 认领不了");
  assert.equal(canonical(null), null);
  assert.equal(canonical("not a url"), null);
});

test("recordVisit is LRU-front and capped at 50, carrying the group name", async () => {
  ownWorld([]);
  for (let i = 0; i < 55; i++) {
    await recordVisit(`https://a.test/p${i}`, "proj");
  }
  const urls = await registrySet();
  assert.equal(urls.size, 50);
  assert.ok(urls.has("https://a.test/p54"), "最新在前，最旧的被淘汰");
  assert.ok(!urls.has("https://a.test/p0"));
  await recordVisit("https://a.test/p10", "proj");
  assert.equal((await registrySet()).size, 50);
  clearChrome();
});

test("recordVisit failure (broken storage) never takes the command down", async () => {
  ownWorld([], {
    extra: {
      storage: {
        local: {
          get: async () => ({}),
          set: async () => {
            throw new Error("QUOTA_BYTES quota exceeded");
          },
        },
        session: { get: async () => ({}), set: async () => {} },
      },
    },
  });
  await recordVisit("https://a.test/p", "proj"); // 不抛就是通过
  clearChrome();
});

test("membership in any browse/* group is ownership: isOwnTab + enforceOwnTab", async () => {
  // tab 7 在 browse/default 组里；tab 8 在别人的组（999）里；tab 9 不在任何组
  const { tabs } = ownWorld([
    { id: 7, url: "https://a.test/", groupId: 500 },
    { id: 8, url: "https://b.test/", groupId: 999 },
    { id: 9, url: "https://c.test/" },
  ]);
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 7)!), true);
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 8)!), false, "非 browse 组不算");
  assert.equal(await isOwnTab(tabs.find((t) => t.id === 9)!), false);
  await enforceOwnTab(7); // 通过即不抛
  const err = await rejectsWith(() => enforceOwnTab(9), "no such frame");
  assert.match(err.message, /not your page/);
  assert.match(err.message, /browse tab adopt/);
  await rejectsWith(() => enforceOwnTab(9999), "no such frame");
  clearChrome();
});

test("reclaim rebuilds browse/<name> groups and pulls registry matches back", async () => {
  const { tabs } = ownWorld([
    { id: 1, url: "https://a.test/report?page=2" }, // 查询串差异不影响认领
    { id: 2, url: "https://b.test/other" }, // 不在登记表：不收
  ], { registry: ["https://a.test/report", "https://gone.test/x"] });
  const { reclaim } = await import("../src/handlers/ownership.ts");
  await reclaim();
  assert.equal(tabs.find((t) => t.id === 1)?.groupId, 500, "收进 browse/default");
  assert.equal(tabs.find((t) => t.id === 2)?.groupId, undefined, "别人的页面不动");
  clearChrome();
});

test("tabsAdopt: refused confirm stops; --group names the group; idempotent when owned", async () => {
  const { tabsAdopt } = await import("../src/handlers/tabs.ts");
  const asked: { action: string; url: string | null }[] = [];
  const { tabs } = ownWorld([
    { id: 9, url: "https://a.test/" },
    { id: 7, url: "https://b.test/", groupId: 500 },
  ]);

  setConfirmHook(async (request) => {
    asked.push({ action: request.action, url: request.url });
    return false;
  });
  await rejectsWith(() => tabsAdopt({ context: "9", group: "proj" }), "lg:user rejected");
  assert.equal(tabs.find((t) => t.id === 9)?.groupId, undefined, "拒了就没收编");
  assert.deepEqual(asked, [{ action: "adoptTab", url: "https://a.test/" }]);

  setConfirmHook(async () => true);
  const adopted = await tabsAdopt({ context: "9", group: "proj" });
  assert.equal(adopted.group, "proj");
  assert.equal(tabs.find((t) => t.id === 9)?.groupId, 500, "收进 browse 组");

  asked.length = 0;
  const again = await tabsAdopt({ context: "7" });
  assert.equal(again.group, "default");
  assert.deepEqual(asked, [], "已在 browse/* 组：幂等，不再弹确认");

  await rejectsWith(() => tabsAdopt({}), "invalid argument");
});

test("tabsAdopt finds foreign tabs by matchUrl over the whole browser", async () => {
  const { tabsAdopt } = await import("../src/handlers/tabs.ts");
  const { tabs } = ownWorld([{ id: 9, url: "https://c.test/private" }]);
  const adopted = await tabsAdopt({ matchUrl: "*c.test*" });
  assert.equal(adopted.context, "9");
  assert.equal(adopted.url, "https://c.test/private");
  assert.equal(tabs.find((t) => t.id === 9)?.groupId, 500);
});
