import assert from "node:assert/strict";
import test from "node:test";
import { setConfirmHook } from "../src/handlers/confirm.ts";
import {
  canonical,
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

test("recordVisit is LRU-front and capped at 50", async () => {
  ownWorld([]);
  for (let i = 0; i < 55; i++) {
    await recordVisit(`https://a.test/p${i}`);
  }
  const urls = await registrySet();
  assert.equal(urls.size, 50);
  assert.ok(urls.has("https://a.test/p54"), "最新在前，最旧的被淘汰");
  assert.ok(!urls.has("https://a.test/p0"));

  // 重复访问同一页：移到表头，不产生第二条
  await recordVisit("https://a.test/p10");
  const again = await registrySet();
  assert.equal(again.size, 50);
  assert.ok(again.has("https://a.test/p10"));
  clearChrome();
});

test("browser restart reclaims registry-matching tabs into the rebuilt group", async () => {
  const { tabs } = ownWorld(
    [
      { id: 1, url: "https://a.test/report?page=2" }, // 查询串差异不影响认领（origin+path）
      { id: 2, url: "https://b.test/other" }, // 不在登记表：不收
    ],
    { registry: ["https://a.test/report", "https://gone.test/x"] },
  );
  // ownWorld 的 session 是空的 = 浏览器刚重启过。force ensureOwn 走重建+认领：
  // 查到现存的 browse 组，把 tab 1 收进去，tab 2 不动。
  const { ensureOwn } = await import("../src/handlers/ownership.ts");
  await ensureOwn();
  assert.equal(tabs.find((t) => t.id === 1)?.groupId, 500);
  assert.equal(tabs.find((t) => t.id === 2)?.groupId, undefined);
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
  await recordVisit("https://a.test/p"); // 不抛就是通过
  clearChrome();
});

test("tabsAdopt: refused confirm stops the adoption, idempotent when already owned", async () => {
  const { tabsAdopt } = await import("../src/handlers/tabs.ts");
  const asked: { action: string; url: string | null }[] = [];
  // tab 9 是别人的页面，tab 7 已经在自己的组里
  const { tabs } = ownWorld([
    { id: 9, url: "https://a.test/", groupId: undefined },
    { id: 7, url: "https://b.test/", groupId: 500 },
  ]);

  setConfirmHook(async (request) => {
    asked.push({ action: request.action, url: request.url });
    return false;
  });
  await rejectsWith(() => tabsAdopt({ context: "9" }), "lg:user rejected");
  assert.equal(tabs.find((t) => t.id === 9)?.groupId, undefined, "拒了就没收编");
  assert.deepEqual(asked, [{ action: "adoptTab", url: "https://a.test/" }]);

  setConfirmHook(async () => true);
  const adopted = await tabsAdopt({ context: "9" });
  assert.equal(adopted.context, "9");
  assert.equal(tabs.find((t) => t.id === 9)?.groupId, 500, "收编进自己的组");

  // 已在组里的页面：幂等成功，且不再弹确认
  asked.length = 0;
  const again = await tabsAdopt({ context: "7" });
  assert.equal(again.context, "7");
  assert.deepEqual(asked, []);

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
