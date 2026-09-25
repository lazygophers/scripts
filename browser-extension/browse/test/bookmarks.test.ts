import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  bookmarksCreate,
  bookmarksRemove,
  bookmarksSearch,
} from "../src/handlers/bookmarks.ts";
import { setConfirmHook } from "../src/handlers/confirm.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

/** 只装用例点名的 namespace：缺席正好测到 requireApi 的拒绝路径。 */
function chromeWith(extra: Any, config: Any = DEFAULTS): void {
  installChrome({ ...extra });
  storageMock({ [CONFIG_KEY]: config });
  setConfirmHook(async () => true);
}

/** 记录下每次调用参数的书签桩。 */
function bookmarksApi() {
  const calls: { fn: string; arg: unknown }[] = [];
  return {
    calls,
    api: {
      search: async (arg: unknown) => {
        calls.push({ fn: "search", arg });
        return [{ id: "1", title: "命中" }];
      },
      create: async (arg: unknown) => {
        calls.push({ fn: "create", arg });
        return { id: "9", ...(arg as Any) };
      },
      remove: async (arg: unknown) => void calls.push({ fn: "remove", arg }),
      removeTree: async (arg: unknown) => void calls.push({ fn: "removeTree", arg }),
    },
  };
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("bookmarksSearch", () => {
  it("refuses when the browser has no bookmarks API", async () => {
    chromeWith({});
    await rejectsWith(() => bookmarksSearch({}), "unsupported operation");
  });

  it("passes a query string straight through", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    const got = await bookmarksSearch({ query: "笔记" });
    assert.deepEqual(calls[0], { fn: "search", arg: "笔记" });
    assert.deepEqual(got, { nodes: [{ id: "1", title: "命中" }] });
  });

  it("builds an object query from url and title when query is absent", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksSearch({ url: "https://e.test/", title: "标题" });
    assert.deepEqual(calls[0].arg, { url: "https://e.test/", title: "标题" });
  });

  it("drops non-string url and title instead of forwarding them", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksSearch({ url: 42, title: null });
    assert.deepEqual(calls[0].arg, {});
  });

  it("rejects a non-string query", async () => {
    const { api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await rejectsWith(() => bookmarksSearch({ query: 7 }), "invalid argument");
  });

  it("refuses when the user denies reading bookmarks", async () => {
    // 默认 confirm_mode 是 silent（直接放行），要测拒绝就得把模式调到 always
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => bookmarksSearch({}), "lg:user rejected");
    assert.equal(calls.length, 0, "拒绝要发生在真正读之前");
  });
});

describe("bookmarksCreate", () => {
  it("creates a folder when url is omitted", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksCreate({ title: "文件夹" });
    assert.deepEqual(calls[0].arg, { title: "文件夹" });
  });

  it("forwards url, parentId and index", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    const got = await bookmarksCreate({
      url: "https://e.test/a",
      title: "A",
      parentId: "2",
      index: 0,
    });
    assert.deepEqual(calls[0].arg, {
      url: "https://e.test/a",
      title: "A",
      parentId: "2",
      index: 0,
    });
    assert.equal((got.node as Any).id, "9");
  });

  it("drops a non-string parentId and a non-number index", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksCreate({ parentId: 2, index: "0" });
    assert.deepEqual(calls[0].arg, {});
  });

  it("rejects a non-string url", async () => {
    const { api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await rejectsWith(() => bookmarksCreate({ url: 1 }), "invalid argument");
  });

  it("refuses when the user denies writing bookmarks", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => bookmarksCreate({ url: "https://e.test/a" }), "lg:user rejected");
    assert.equal(calls.length, 0);
  });
});

describe("bookmarksRemove", () => {
  it("needs a bookmark id", async () => {
    const { api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await rejectsWith(() => bookmarksRemove({}), "invalid argument");
  });

  it("rejects an empty id rather than removing something unnamed", async () => {
    const { api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await rejectsWith(() => bookmarksRemove({ id: "" }), "invalid argument");
  });

  it("removes one node by default", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    assert.deepEqual(await bookmarksRemove({ id: "7" }), { removed: "7" });
    assert.deepEqual(calls[0], { fn: "remove", arg: "7" });
  });

  it("uses removeTree only when recursive is exactly true", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksRemove({ id: "7", recursive: true });
    assert.equal(calls[0].fn, "removeTree");
  });

  it("treats a truthy non-true recursive as a plain remove", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api });
    await bookmarksRemove({ id: "7", recursive: "yes" });
    assert.equal(calls[0].fn, "remove");
  });

  it("refuses when the user denies the removal", async () => {
    const { calls, api } = bookmarksApi();
    chromeWith({ bookmarks: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => bookmarksRemove({ id: "7" }), "lg:user rejected");
    assert.equal(calls.length, 0);
  });
});
