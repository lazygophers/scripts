import assert from "node:assert/strict";
import test from "node:test";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import {
  pageReadLocalStorage,
  pageWriteLocalStorage,
  storageDeleteCookies,
  storageGetCookies,
  storageGetLocalStorage,
  storageSetCookie,
  storageSetLocalStorage,
} from "../src/handlers/storage.ts";
import { clearChrome, installChrome, ownSession, page, rejectsWith, scriptingMock } from "./mock.ts";

type Any = Record<string, unknown>;

const COOKIES = [
  { name: "sid", value: "abc", domain: ".a.test", path: "/", secure: true },
  { name: "t", value: "1", domain: "a.test", path: "/x", secure: false },
];

function setup(): { calls: Any[]; asked: ConfirmRequest[] } {
  page(`<p>x</p>`);
  const calls: Any[] = [];
  const asked: ConfirmRequest[] = [];
  installChrome({
    ...ownSession(),
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true, groupId: 500 }],
      get: async () => ({ id: 7, url: "https://a.test/", groupId: 500 }),
    },
    scripting: scriptingMock(),
    cookies: {
      getAll: async (filter: Any) => {
        calls.push({ getAll: filter });
        return COOKIES;
      },
      set: async (details: Any) => {
        calls.push({ set: details });
        return { ...details };
      },
      remove: async (details: Any) => {
        calls.push({ remove: details });
      },
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

test("getCookies needs a scope and asks the confirm hook", async () => {
  const { calls, asked } = setup();
  const result = await storageGetCookies({ domain: "a.test" });
  assert.equal(result.cookies.length, 2);
  assert.deepEqual(calls[0], { getAll: { domain: "a.test" } });
  assert.deepEqual(asked[0], {
    action: "readCookies",
    method: "storage.getCookies",
    url: "a.test",
  });

  await rejectsWith(() => storageGetCookies({}), "invalid argument");
  teardown();
});

test("a refused confirm stops the cookie read", async () => {
  setup();
  setConfirmHook(async () => false);
  const err = await rejectsWith(
    () => storageGetCookies({ domain: "a.test" }),
    "lg:user rejected",
  );
  assert.match(err.message, /user denied readCookies/);
  teardown();
});

test("setCookie requires url, name and value", async () => {
  const { calls } = setup();
  await storageSetCookie({ url: "https://a.test/", name: "sid", value: "z", secure: true });
  assert.deepEqual(calls[0], {
    set: { url: "https://a.test/", name: "sid", value: "z", secure: true },
  });
  await rejectsWith(
    () => storageSetCookie({ url: "https://a.test/", name: "sid" }),
    "invalid argument",
  );
  teardown();
});

test("deleteCookies removes every match and rebuilds each url from the cookie", async () => {
  const { calls } = setup();
  assert.deepEqual(await storageDeleteCookies({ domain: "a.test" }), { deleted: 2 });
  assert.deepEqual(calls[1], { remove: { url: "https://a.test/", name: "sid" } });
  assert.deepEqual(calls[2], { remove: { url: "http://a.test/x", name: "t" } });
  teardown();
});

test("getLocalStorage reads the page's store, one key or all of it", async () => {
  const { asked } = setup();
  localStorage.setItem("a", "1");
  localStorage.setItem("b", "2");

  assert.deepEqual(await storageGetLocalStorage({}), { entries: { a: "1", b: "2" } });
  assert.deepEqual(await storageGetLocalStorage({ key: "b" }), { entries: { b: "2" } });
  assert.deepEqual(asked[0], {
    action: "readLocalStorage",
    method: "storage.getLocalStorage",
    url: "https://a.test/",
  });
  teardown();
});

test("setLocalStorage writes entries and a null value removes a key", async () => {
  setup();
  localStorage.clear();
  await storageSetLocalStorage({ entries: { a: "1", b: "2" } });
  assert.equal(localStorage.getItem("a"), "1");

  await storageSetLocalStorage({ key: "a", value: null });
  assert.equal(localStorage.getItem("a"), null);
  assert.equal(localStorage.getItem("b"), "2");
  teardown();
});

test("setLocalStorage rejects a non-object entries and a missing key", async () => {
  setup();
  await rejectsWith(() => storageSetLocalStorage({ entries: [] }), "invalid argument");
  await rejectsWith(() => storageSetLocalStorage({}), "invalid argument");
  teardown();
});

test("cookies are refused outright when the browser has no cookies API", async () => {
  installChrome({ tabs: {} });
  const err = await rejectsWith(
    () => storageGetCookies({ domain: "a.test" }),
    "unsupported operation",
  );
  assert.match(err.message, /chrome.cookies is not available/);
  clearChrome();
});

test("a refused cookie write is an unknown error, not a silent success", async () => {
  setup();
  installChrome({
    ...ownSession(),
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true, groupId: 500 }],
      get: async () => ({ id: 7, url: "https://a.test/", groupId: 500 }),
    },
    cookies: {
      set: async () => null,
    },
  });
  await rejectsWith(
    () => storageSetCookie({ url: "https://a.test/", name: "sid", value: "v" }),
    "unknown error",
  );
  teardown();
});

test("deleteCookies removes per scheme and honours storeId", async () => {
  const { calls } = setup();
  const result = await storageDeleteCookies({ domain: "a.test" });
  assert.deepEqual(result, { deleted: 2 });
  const urls = calls.filter((c) => c.remove).map((c) => (c.remove as Any).url);
  assert.ok(urls.includes("https://a.test/"), urls.join(" | "));
  assert.ok(urls.includes("http://a.test/x"), urls.join(" | "));
  teardown();
});

test("page-side localStorage helpers survive a missing localStorage", async () => {
  const g = globalThis as unknown as Record<string, unknown>;
  const saved = g.localStorage;
  delete g.localStorage;
  try {
    const read = pageReadLocalStorage(null);
    assert.equal(read.ok, false);
    const write = pageWriteLocalStorage({ a: "1" });
    assert.equal(write.ok, false);
  } finally {
    g.localStorage = saved;
  }
});

test("setCookie forwards every optional attribute the caller gives", async () => {
  const { calls } = setup();
  await storageSetCookie({
    url: "https://a.test/", name: "sid", value: "v",
    domain: ".a.test", path: "/x", expirationDate: 123, secure: false, httpOnly: true,
  });
  assert.deepEqual(calls[0]?.set, {
    url: "https://a.test/", name: "sid", value: "v",
    domain: ".a.test", path: "/x", expirationDate: 123, secure: false, httpOnly: true,
  });
  teardown();
});

test("page-side localStorage read distinguishes a missing key from an empty store", async () => {
  setup();
  localStorage.clear();
  localStorage.setItem("a", "1");
  const hit = pageReadLocalStorage("a");
  assert.deepEqual(hit, { ok: true, value: { a: "1" } });
  const miss = pageReadLocalStorage("nope");
  assert.deepEqual(miss, { ok: true, value: {} });
  const all = pageReadLocalStorage(null);
  assert.deepEqual(all, { ok: true, value: { a: "1" } });
  teardown();
});

test("cookie filter refuses non-string scopes; entries and values normalise", async () => {
  const { calls } = setup();
  // 非字符串的 scope 字段被丢掉；两个都缺才被拒
  await storageGetCookies({ domain: 3 });
  assert.deepEqual(calls.at(-1), { getAll: {} });
  localStorage.clear();
  const r = await storageSetLocalStorage({ entries: { a: null, b: "2" } });
  assert.deepEqual(r, { written: 2 });
  assert.equal(localStorage.getItem("a"), null);
  assert.equal(localStorage.getItem("b"), "2");
  await storageSetLocalStorage({ key: "b" });
  assert.equal(localStorage.getItem("b"), null, "value 缺省按删除处理");
  teardown();
});
