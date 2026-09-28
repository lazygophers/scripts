import assert from "node:assert/strict";
import test from "node:test";
import { cacheGet, cacheList, cachePut, invalidateTab } from "../src/handlers/cache.ts";
import { resetOwnership } from "../src/handlers/ownership.ts";
import { clearChrome, ownWorld, rejectsWith } from "./mock.ts";

test.afterEach(() => {
  resetOwnership();
  clearChrome();
});

test("put then get roundtrips, index 0 is the newest", async () => {
  ownWorld([{ id: 7, url: "https://a.test/", groupId: 500, active: true }]);
  assert.deepEqual(await cachePut({ kind: "text", body: "旧" }), { cached: 1 });
  assert.deepEqual(await cachePut({ kind: "text", body: "新" }), { cached: 2 });
  assert.equal((await cacheGet({ context: "7" })).body, "新");
  assert.equal((await cacheGet({ context: "7", index: 1 })).body, "旧");
});

test("get with no cached entry is an error naming the likely cause", async () => {
  ownWorld([{ id: 7, url: "https://a.test/", groupId: 500, active: true }]);
  const err = await rejectsWith(() => cacheGet({ context: "7" }), "no such frame");
  assert.match(err.message, /navigation clears the cache/);
});

test("a frame context is refused", async () => {
  ownWorld([{ id: 7, url: "https://a.test/", groupId: 500 }]);
  await rejectsWith(() => cachePut({ context: "7.2", kind: "text", body: "x" }),
    "unsupported operation");
});

test("the ring keeps the last 5 per tab", async () => {
  ownWorld([{ id: 7, url: "https://a.test/", groupId: 500, active: true }]);
  for (let i = 0; i < 7; i++) {
    await cachePut({ kind: "text", body: `v${i}` });
  }
  assert.deepEqual(await cachePut({ kind: "text", body: "v7" }), { cached: 5 });
  assert.equal((await cacheGet({})).body, "v7");
  assert.equal((await cacheGet({ index: 4 })).body, "v3", "v0~v2 被环形淘汰");
  await rejectsWith(() => cacheGet({ index: 5 }), "no such frame");
});

test("invalidateTab clears the page; list reports what is left", async () => {
  ownWorld([
    { id: 7, url: "https://a.test/", groupId: 500, active: true },
    { id: 8, url: "https://b.test/", groupId: 500 },
  ]);
  await cachePut({ kind: "text", body: "a" });
  await cachePut({ context: "8", kind: "html", body: "b" });
  const listed = await cacheList();
  assert.equal(listed.pages.length, 2);
  await invalidateTab(7);
  const after = await cacheList();
  assert.deepEqual(after.pages.map((p) => p.context), ["8"]);
  assert.equal(after.pages[0]?.kind, "html");
});

test("put on broken storage reports cached: 0 instead of failing", async () => {
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
  // tab 7 得先进得了自己的组（extra 的 storage 替换了默认件，session 空但组能按标题找到）
  assert.deepEqual(await cachePut({ context: "7", kind: "text", body: "x" }), { cached: 0 });
});
