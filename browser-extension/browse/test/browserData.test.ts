import assert from "node:assert/strict";
import test from "node:test";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import {
  downloadsCancel,
  downloadsList,
  downloadsStart,
} from "../src/handlers/downloads.ts";
import { historyDelete, historySearch } from "../src/handlers/history.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

function setup(): { calls: Any[]; asked: ConfirmRequest[] } {
  const calls: Any[] = [];
  const asked: ConfirmRequest[] = [];
  // 归属（票 02）：history 只回自己页面的记录，夹具项要在登记表里
  storageMock({ "browse:ownership": ["https://a.test"] });
  installChrome({
    history: {
      search: async (query: Any) => {
        calls.push({ historySearch: query });
        return [{ url: "https://a.test/", title: "A" }];
      },
      deleteUrl: async (details: Any) => calls.push({ deleteUrl: details }),
      deleteRange: async (range: Any) => calls.push({ deleteRange: range }),
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
  await rejectsWith(
    () => downloadsStart({ url: "https://a.test/f" }),
    "unsupported operation",
  );
  clearChrome();
});
