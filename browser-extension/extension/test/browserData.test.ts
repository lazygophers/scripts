import assert from "node:assert/strict";
import test from "node:test";
import {
  bookmarksCreate,
  bookmarksRemove,
  bookmarksSearch,
} from "../src/handlers/bookmarks.ts";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import {
  downloadsCancel,
  downloadsList,
  downloadsStart,
} from "../src/handlers/downloads.ts";
import { historyDelete, historySearch } from "../src/handlers/history.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

function setup(): { calls: Any[]; asked: ConfirmRequest[] } {
  const calls: Any[] = [];
  const asked: ConfirmRequest[] = [];
  installChrome({
    history: {
      search: async (query: Any) => {
        calls.push({ historySearch: query });
        return [{ url: "https://a.test/", title: "A" }];
      },
      deleteUrl: async (details: Any) => calls.push({ deleteUrl: details }),
      deleteRange: async (range: Any) => calls.push({ deleteRange: range }),
    },
    bookmarks: {
      search: async (query: unknown) => {
        calls.push({ bookmarkSearch: query });
        return [{ id: "1", title: "A" }];
      },
      create: async (node: Any) => {
        calls.push({ bookmarkCreate: node });
        return { id: "2", ...node };
      },
      remove: async (id: string) => calls.push({ bookmarkRemove: id }),
      removeTree: async (id: string) => calls.push({ bookmarkRemoveTree: id }),
    },
    downloads: {
      download: async (options: Any) => {
        calls.push({ download: options });
        return 11;
      },
      search: async (query: Any) => {
        calls.push({ downloadSearch: query });
        return [{ id: 11, state: "complete" }];
      },
      cancel: async (id: number) => calls.push({ downloadCancel: id }),
    },
  });
  setConfirmHook(async (request) => {
    asked.push(request);
    return true;
  });
  return { calls, asked };
}

function teardown(): void {
  setConfirmHook(async () => true);
  clearChrome();
}

test("history search confirms first and passes the window through", async () => {
  const { calls, asked } = setup();
  const result = await historySearch({ text: "orders", maxResults: 5 });
  assert.equal(result.items.length, 1);
  assert.deepEqual(calls[0], { historySearch: { text: "orders", maxResults: 5 } });
  assert.deepEqual(asked[0], {
    action: "readHistory",
    method: "lg:history.search",
    url: null,
  });
  teardown();
});

test("a refused confirm blocks the history read", async () => {
  setup();
  setConfirmHook(async () => false);
  await rejectsWith(() => historySearch({}), "lg:user rejected");
  teardown();
});

test("history delete takes a url or a full range, and never everything", async () => {
  const { calls } = setup();
  assert.deepEqual(await historyDelete({ url: "https://a.test/" }), { deleted: "url" });
  assert.deepEqual(calls[0], { deleteUrl: { url: "https://a.test/" } });

  assert.deepEqual(await historyDelete({ startTime: 1, endTime: 2 }), { deleted: "range" });
  assert.deepEqual(calls[1], { deleteRange: { startTime: 1, endTime: 2 } });

  const err = await rejectsWith(() => historyDelete({ startTime: 1 }), "invalid argument");
  assert.match(err.message, /deleting all history is not supported/);
  teardown();
});

test("bookmark search takes a free-text query or a structured one", async () => {
  const { calls } = setup();
  await bookmarksSearch({ query: "docs" });
  assert.deepEqual(calls[0], { bookmarkSearch: "docs" });
  await bookmarksSearch({ url: "https://a.test/" });
  assert.deepEqual(calls[1], { bookmarkSearch: { url: "https://a.test/" } });
  teardown();
});

test("bookmark create keeps a folder (no url) distinct from a bookmark", async () => {
  const { calls, asked } = setup();
  await bookmarksCreate({ title: "folder" });
  assert.deepEqual(calls[0], { bookmarkCreate: { title: "folder" } });
  assert.deepEqual(asked[0], {
    action: "writeBookmarks",
    method: "lg:bookmarks.create",
    url: null,
  });

  await bookmarksCreate({ title: "A", url: "https://a.test/", parentId: "1" });
  assert.deepEqual(calls[1], {
    bookmarkCreate: { url: "https://a.test/", title: "A", parentId: "1" },
  });
  teardown();
});

test("bookmark remove needs recursive: true before it deletes children", async () => {
  const { calls } = setup();
  await bookmarksRemove({ id: "9" });
  assert.deepEqual(calls[0], { bookmarkRemove: "9" });
  await bookmarksRemove({ id: "9", recursive: true });
  assert.deepEqual(calls[1], { bookmarkRemoveTree: "9" });
  await rejectsWith(() => bookmarksRemove({}), "invalid argument");
  teardown();
});

test("download start confirms with the target url and returns the download id", async () => {
  const { calls, asked } = setup();
  assert.deepEqual(await downloadsStart({ url: "https://a.test/f.zip" }), { download: 11 });
  assert.deepEqual(calls[0], { download: { url: "https://a.test/f.zip" } });
  assert.deepEqual(asked[0], {
    action: "download",
    method: "lg:downloads.start",
    url: "https://a.test/f.zip",
  });
  teardown();
});

test("a refused confirm stops the download before it touches the disk", async () => {
  const { calls } = setup();
  setConfirmHook(async () => false);
  await rejectsWith(() => downloadsStart({ url: "https://a.test/f.zip" }), "lg:user rejected");
  assert.equal(calls.length, 0);
  teardown();
});

test("download list and cancel pass their arguments through", async () => {
  const { calls } = setup();
  const listed = await downloadsList({ state: "complete", limit: 5 });
  assert.equal(listed.downloads.length, 1);
  assert.deepEqual(calls[0], { downloadSearch: { state: "complete", limit: 5 } });

  assert.deepEqual(await downloadsCancel({ id: 11 }), { cancelled: 11 });
  await rejectsWith(() => downloadsCancel({ id: "11" }), "invalid argument");
  teardown();
});

test("a browser without these namespaces refuses explicitly instead of degrading", async () => {
  installChrome({});
  await rejectsWith(() => historySearch({}), "unsupported operation");
  await rejectsWith(() => bookmarksSearch({}), "unsupported operation");
  await rejectsWith(
    () => downloadsStart({ url: "https://a.test/f" }),
    "unsupported operation",
  );
  clearChrome();
});
