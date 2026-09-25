import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setConfirmHook } from "../src/handlers/confirm.ts";
import { historyDelete, historySearch } from "../src/handlers/history.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

function chromeWith(extra: Any, config: Any = DEFAULTS): void {
  installChrome({ ...extra });
  storageMock({ [CONFIG_KEY]: config });
  setConfirmHook(async () => true);
}

function historyApi() {
  const calls: { fn: string; arg: unknown }[] = [];
  return {
    calls,
    api: {
      search: async (arg: unknown) => {
        calls.push({ fn: "search", arg });
        return [{ id: "1", url: "https://e.test/a" }];
      },
      deleteUrl: async (arg: unknown) => void calls.push({ fn: "deleteUrl", arg }),
      deleteRange: async (arg: unknown) => void calls.push({ fn: "deleteRange", arg }),
    },
  };
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("historySearch", () => {
  it("refuses when the browser has no history API", async () => {
    chromeWith({});
    await rejectsWith(() => historySearch({}), "unsupported operation");
  });

  it("defaults text to the empty string (= everything)", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    const got = await historySearch({});
    assert.deepEqual(calls[0].arg, { text: "" });
    assert.deepEqual(got, { items: [{ id: "1", url: "https://e.test/a" }] });
  });

  it("forwards the numeric window and maxResults", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    await historySearch({ text: "词", startTime: 1, endTime: 2, maxResults: 5 });
    assert.deepEqual(calls[0].arg, { text: "词", startTime: 1, endTime: 2, maxResults: 5 });
  });

  it("drops non-numeric time bounds instead of forwarding them", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    await historySearch({ startTime: "1", maxResults: null });
    assert.deepEqual(calls[0].arg, { text: "" });
  });

  it("rejects a non-string text", async () => {
    const { api } = historyApi();
    chromeWith({ history: api });
    await rejectsWith(() => historySearch({ text: 1 }), "invalid argument");
  });

  it("refuses when the user denies reading history", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => historySearch({}), "lg:user rejected");
    assert.equal(calls.length, 0, "拒绝要发生在真正读之前");
  });
});

describe("historyDelete", () => {
  it("deletes one url and says so", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    assert.deepEqual(await historyDelete({ url: "https://e.test/a" }), { deleted: "url" });
    assert.deepEqual(calls[0], { fn: "deleteUrl", arg: { url: "https://e.test/a" } });
  });

  it("hands the target url to the confirm hook", async () => {
    const seen: Any[] = [];
    const { api } = historyApi();
    chromeWith({ history: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async (request) => {
      seen.push(request as unknown as Any);
      return true;
    });
    await historyDelete({ url: "https://e.test/a" });
    assert.equal(seen[0].url, "https://e.test/a");
    assert.equal(seen[0].action, "writeHistory");
  });

  it("deletes a time range when both bounds are given", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    assert.deepEqual(await historyDelete({ startTime: 10, endTime: 20 }), { deleted: "range" });
    assert.deepEqual(calls[0], { fn: "deleteRange", arg: { startTime: 10, endTime: 20 } });
  });

  it("refuses with no arguments — deleting everything is not offered", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api });
    await rejectsWith(() => historyDelete({}), "invalid argument");
    assert.equal(calls.length, 0);
  });

  it("refuses a half-specified range", async () => {
    const { api } = historyApi();
    chromeWith({ history: api });
    await rejectsWith(() => historyDelete({ startTime: 10 }), "invalid argument");
  });

  it("treats an empty url as not given", async () => {
    const { api } = historyApi();
    chromeWith({ history: api });
    await rejectsWith(() => historyDelete({ url: "" }), "invalid argument");
  });

  it("refuses when the user denies the deletion", async () => {
    const { calls, api } = historyApi();
    chromeWith({ history: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => historyDelete({ url: "https://e.test/a" }), "lg:user rejected");
    assert.equal(calls.length, 0);
  });
});
