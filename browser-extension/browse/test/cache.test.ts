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

test("put needs both kind and body", async () => {
  ownWorld([{ id: 7, url: "https://a.test/", groupId: 500, active: true }]);
  await rejectsWith(() => cachePut({ kind: "text" }), "invalid argument");
  await rejectsWith(() => cachePut({ body: "x" }), "invalid argument");
});

test("the global cap drops whole oldest pages until back under 100", async () => {
  const many = Array.from({ length: 25 }, (_, i) => ({
    id: i + 1,
    url: `https://t${i}.test/`,
    groupId: 500,
  }));
  ownWorld(many);
  // 25 页 × 5 份 = 125 > 100：最旧的 5 页整页丢，回到 100
  for (let round = 0; round < 5; round++) {
    for (let tab = 1; tab <= 25; tab++) {
      await cachePut({ context: String(tab), kind: "text", body: `t${tab}-r${round}` });
    }
  }
  const listed = await cacheList();
  assert.equal(listed.pages.length, 20, "100 条上限按整页淘汰");
  for (const page of listed.pages) {
    assert.equal(page.entries, 5);
  }
  await rejectsWith(() => cacheGet({ context: "1" }), "no such frame", "最旧的页被整页丢了");
});

test("invalidateTab swallows broken storage", async () => {
  ownWorld([], {
    extra: {
      storage: {
        local: {
          get: async () => {
            throw new Error("gone");
          },
          set: async () => {},
        },
        session: { get: async () => ({}), set: async () => {} },
      },
    },
  });
  await assert.doesNotReject(() => invalidateTab(7));
});

test("cacheList tolerates a tab entry with an empty ring", async () => {
  ownWorld([], { local: { "browse:pagecache": { "77": [] } } });
  const listed = await cacheList();
  assert.deepEqual(listed.pages, [{ context: "77", entries: 0, latest: 0, kind: "" }]);
});
