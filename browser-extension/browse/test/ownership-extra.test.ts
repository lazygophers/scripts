import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { canonical, ensureReclaimed, recordVisit, resetOwnership } from "../src/handlers/ownership.ts";
import { clearChrome, installChrome } from "./mock.ts";

type Any = Record<string, unknown>;

function world(registry: unknown = [], set: (items: Any) => void = () => {}): { store: Any } {
  const store: Any = { "browse:ownership": registry };
  installChrome({
    storage: {
      local: {
        get: async () => store,
        set: async (items: Any) => {
          Object.assign(store, items);
          set(items);
        },
        remove: async () => {},
      },
      session: { get: async () => ({}), set: async () => {} },
    },
  });
  return { store };
}

afterEach(() => {
  resetOwnership();
  clearChrome();
});

test("recordVisit normalises to origin+path and dedupes the head", async () => {
  const { store } = world([{ u: "https://a.test/x", g: "default" }]);
  await recordVisit("https://a.test/x?query=1#frag", "default");
  assert.deepEqual(store["browse:ownership"], [{ u: "https://a.test/x", g: "default" }],
    "表头同页同组：不写");
  await recordVisit("https://a.test/x", "work");
  assert.deepEqual(store["browse:ownership"], [
    { u: "https://a.test/x", g: "work" },
  ], "同页换组：前插覆盖");
});

test("recordVisit reorders the LRU and caps at 50", async () => {
  const registry = Array.from({ length: 50 }, (_, i) => ({ u: `https://t${i}.test/`, g: "g" }));
  const { store } = world(registry);
  await recordVisit("https://t49.test/", "g");
  const list = store["browse:ownership"] as { u: string }[];
  assert.equal(list.length, 50, "上限 50");
  assert.equal(list[0]?.u, "https://t49.test/");
  assert.equal(list[1]?.u, "https://t0.test/", "t49 原来在队尾，提到队头后 t0 顶上");
});

test("recordVisit ignores opaque or broken URLs and swallows broken storage", async () => {
  const { store } = world();
  await recordVisit(null, "g");
  await recordVisit("", "g");
  await recordVisit("about:blank", "g");
  await recordVisit("not a url", "g");
  assert.deepEqual(store["browse:ownership"], [], "什么都没写");
  installChrome({
    storage: {
      local: {
        get: async () => {
          throw new Error("gone");
        },
        set: async () => {},
      },
      session: { get: async () => ({}), set: async () => {} },
    },
  });
  await assert.doesNotReject(() => recordVisit("https://a.test/", "g"));
});

test("canonical strips queries and refuses opaque origins", () => {
  assert.equal(canonical("https://a.test/p?q=1#f"), "https://a.test/p");
  assert.equal(canonical("data:text/html,x"), null);
  assert.equal(canonical(null), null);
  assert.equal(canonical(undefined), null);
});

test("ensureReclaimed runs once per service worker life", async () => {
  let reads = 0;
  installChrome({
    tabGroups: {
      query: async () => [],
      get: async () => ({}),
      update: async () => ({}),
    },
    tabs: { query: async () => [{ id: 1, url: "https://a.test/", groupId: 500 }], group: async () => 1, ungroup: async () => {} },
    storage: {
      session: { get: async () => ({ "browse:own": { groupId: 500 } }), set: async () => {} },
      local: {
        get: async () => {
          reads += 1;
          return { "browse:ownership": [{ u: "https://a.test/", g: "default" }] };
        },
        set: async () => {},
      },
    },
  });
  await ensureReclaimed();
  await ensureReclaimed();
  await ensureReclaimed();
  assert.equal(reads, 1, "第二次起直接短路");
});
