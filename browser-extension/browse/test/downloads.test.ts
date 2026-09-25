import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setConfirmHook } from "../src/handlers/confirm.ts";
import {
  downloadsCancel,
  downloadsList,
  downloadsOpen,
  downloadsStart,
} from "../src/handlers/downloads.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

function chromeWith(extra: Any, config: Any = DEFAULTS): void {
  installChrome({ ...extra });
  storageMock({ [CONFIG_KEY]: config });
  setConfirmHook(async () => true);
}

/** `withOpen` 单独控制：downloads.open 是另一条权限，requireApi 查的是它本身。 */
function downloadsApi({ withOpen = true } = {}) {
  const calls: { fn: string; arg: unknown }[] = [];
  const api: Any = {
    download: async (arg: unknown) => {
      calls.push({ fn: "download", arg });
      return 11;
    },
    search: async (arg: unknown) => {
      calls.push({ fn: "search", arg });
      return [{ id: 11, state: "complete" }];
    },
    cancel: async (arg: unknown) => void calls.push({ fn: "cancel", arg }),
  };
  if (withOpen) {
    api.open = async (arg: unknown) => void calls.push({ fn: "open", arg });
  }
  return { calls, api };
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("downloadsStart", () => {
  it("refuses when the browser has no downloads API", async () => {
    chromeWith({});
    await rejectsWith(() => downloadsStart({ url: "https://e.test/a.zip" }), "unsupported operation");
  });

  it("needs a url", async () => {
    const { api } = downloadsApi();
    chromeWith({ downloads: api });
    await rejectsWith(() => downloadsStart({}), "invalid argument");
  });

  it("returns the download id", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    assert.deepEqual(await downloadsStart({ url: "https://e.test/a.zip" }), { download: 11 });
    assert.deepEqual(calls[0].arg, { url: "https://e.test/a.zip" });
  });

  it("forwards filename and normalises saveAs to a boolean", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    await downloadsStart({ url: "https://e.test/a.zip", filename: "a.zip", saveAs: "yes" });
    assert.deepEqual(calls[0].arg, {
      url: "https://e.test/a.zip",
      filename: "a.zip",
      saveAs: false,
    });
  });

  it("rejects a non-string filename", async () => {
    const { api } = downloadsApi();
    chromeWith({ downloads: api });
    await rejectsWith(
      () => downloadsStart({ url: "https://e.test/a.zip", filename: 1 }),
      "invalid argument",
    );
  });

  it("refuses when the user denies the download", async () => {
    // 写文件到磁盘是唯一一个离开浏览器的动作，拒绝必须发生在 download 之前
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => downloadsStart({ url: "https://e.test/a.zip" }), "lg:user rejected");
    assert.equal(calls.length, 0);
  });
});

describe("downloadsList", () => {
  it("passes an empty filter through when nothing is given", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    const got = await downloadsList({});
    assert.deepEqual(calls[0].arg, {});
    assert.deepEqual(got, { downloads: [{ id: 11, state: "complete" }] });
  });

  it("forwards id, state, urlRegex and limit", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    await downloadsList({ id: 11, state: "in_progress", urlRegex: "zip$", limit: 5 });
    assert.deepEqual(calls[0].arg, { id: 11, state: "in_progress", urlRegex: "zip$", limit: 5 });
  });

  it("drops filter fields of the wrong type", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    await downloadsList({ id: "11", limit: "5" });
    assert.deepEqual(calls[0].arg, {});
  });

  it("is not gated by confirm — listing reveals nothing new", async () => {
    const { api } = downloadsApi();
    chromeWith({ downloads: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    assert.equal((await downloadsList({})).downloads.length, 1);
  });
});

describe("downloadsCancel", () => {
  it("needs a numeric id", async () => {
    const { api } = downloadsApi();
    chromeWith({ downloads: api });
    await rejectsWith(() => downloadsCancel({ id: "11" }), "invalid argument");
  });

  it("cancels and echoes the id", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    assert.deepEqual(await downloadsCancel({ id: 11 }), { cancelled: 11 });
    assert.deepEqual(calls[0], { fn: "cancel", arg: 11 });
  });
});

describe("downloadsOpen", () => {
  it("refuses when downloads.open is missing, even though downloads exists", async () => {
    const { api } = downloadsApi({ withOpen: false });
    chromeWith({ downloads: api });
    await rejectsWith(() => downloadsOpen({ id: 11 }), "unsupported operation");
  });

  it("needs a numeric id", async () => {
    const { api } = downloadsApi();
    chromeWith({ downloads: api });
    await rejectsWith(() => downloadsOpen({}), "invalid argument");
  });

  it("opens and echoes the id", async () => {
    const { calls, api } = downloadsApi();
    chromeWith({ downloads: api });
    assert.deepEqual(await downloadsOpen({ id: 11 }), { opened: 11 });
    assert.deepEqual(calls[0], { fn: "open", arg: 11 });
  });
});
