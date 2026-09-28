import assert from "node:assert/strict";
import test from "node:test";
import {
  browsingContextActivate,
  browsingContextCaptureScreenshot,
  browsingContextClose,
  browsingContextCreate,
  browsingContextGetTree,
  browsingContextNavigate,
  browsingContextReload,
} from "../src/handlers/browsingContext.ts";
import { resetOwnership } from "../src/handlers/ownership.ts";
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

function tabsMock(tab: Any = {}): { calls: Any[]; chrome: Any } {
  const calls: Any[] = [];
  const state = { id: 7, url: "https://a.test/", active: true, windowId: 3, groupId: 500, ...tab };
  const listeners: ((id: number, info: Any) => void)[] = [];
  const chrome = installChrome({
    tabs: {
      query: async () => [state],
      get: async () => state,
      create: async (opts: Any) => {
        calls.push({ create: opts });
        return { id: 99, windowId: 3 };
      },
      group: async (opts: Any) => {
        calls.push({ group: opts });
        if (opts.groupId === 404) {
          throw new Error("No group with id: 404.");
        }
        return (opts.groupId as number | undefined) ?? 11;
      },
      remove: async (id: number) => {
        calls.push({ remove: id });
      },
      update: async (id: number, opts: Any) => {
        calls.push({ update: [id, opts] });
        return state;
      },
      reload: async (id: number, opts: Any) => {
        calls.push({ reload: [id, opts] });
      },
      captureVisibleTab: async (windowId: number, opts: Any) => {
        calls.push({ capture: [windowId, opts] });
        return "data:image/png;base64,QUJD";
      },
      onUpdated: {
        addListener: (fn: (id: number, info: Any) => void) => {
          listeners.push(fn);
          // The load finishes on the next tick, like a real navigation.
          setTimeout(() => fn(state.id as number, { status: "complete" }), 0);
        },
        removeListener: () => undefined,
      },
    },
    tabGroups: {
      get: async () => ({ id: 500, windowId: 3, title: "browse" }),
      query: async () => [],
      update: async (id: number, opts: Any) => {
        calls.push({ groupUpdate: [id, opts] });
        return { id, ...opts };
      },
    },
    storage: {
      // session 里记着自己的组：ensureOwn 不用建窗口，calls 里的断言才不被污染
      session: {
        get: async () => ({ "browse:own": { groupId: 500 } }),
        set: async () => {},
      },
      local: { get: async () => ({}), set: async () => {} },
    },
    windows: {
      create: async (opts: Any) => {
        calls.push({ windowCreate: opts });
        return { id: 5, tabs: [{ id: 42 }] };
      },
      update: async (id: number, opts: Any) => {
        calls.push({ windowUpdate: [id, opts] });
      },
    },
  });
  return { calls, chrome };
}

test("create opens a tab in the dedicated window and groups it", async () => {
  const { calls } = tabsMock();
  assert.deepEqual(await browsingContextCreate({ url: "https://x.test/" }), {
    context: "99",
  });
  assert.deepEqual(calls[0], { create: { url: "https://x.test/", windowId: 3, active: true } });
  assert.deepEqual(calls[1], { group: { tabIds: [99], groupId: 500 } });
  clearChrome();
});

test("create background opens without focusing, still in the dedicated window", async () => {
  const { calls } = tabsMock();
  await browsingContextCreate({ url: "https://x.test/", background: true });
  assert.deepEqual(calls[0], { create: { url: "https://x.test/", windowId: 3, active: false } });
  clearChrome();
});

test("close and activate address the tab", async () => {
  const { calls } = tabsMock();
  await browsingContextClose({ context: "7" });
  await browsingContextActivate({ context: "7" });
  assert.deepEqual(calls[0], { remove: 7 });
  assert.deepEqual(calls[1], { update: [7, { active: true }] });
  assert.deepEqual(calls[2], { windowUpdate: [3, { focused: true }] });
  clearChrome();
});

test("a frame context is refused by tab-level commands instead of silently widened", async () => {
  tabsMock();
  const err = await rejectsWith(
    () => browsingContextClose({ context: "7.2" }),
    "unsupported operation",
  );
  assert.match(err.message, /drop the \.2 suffix/);
  clearChrome();
});

test("navigate waits for the load and reports the landed url", async () => {
  const { calls } = tabsMock();
  const result = await browsingContextNavigate({ url: "https://x.test/" });
  assert.deepEqual(result, { navigation: null, url: "https://a.test/" });
  assert.deepEqual(calls[0], { update: [7, { url: "https://x.test/" }] });
  clearChrome();
});

test("navigate rejects an empty url", async () => {
  tabsMock();
  await rejectsWith(() => browsingContextNavigate({ url: "" }), "invalid argument");
  clearChrome();
});

test("reload passes ignoreCache through as bypassCache", async () => {
  const { calls } = tabsMock();
  await browsingContextReload({ ignoreCache: true });
  assert.deepEqual(calls[0], { reload: [7, { bypassCache: true }] });
  clearChrome();
});

test("captureScreenshot returns bare base64 and flags that it is viewport only", async () => {
  tabsMock();
  assert.deepEqual(await browsingContextCaptureScreenshot({}), {
    data: "QUJD",
    "lg:viewportOnly": true,
    "lg:activated": false,
  });
  clearChrome();
});

test("captureScreenshot activates an inactive tab and says so", async () => {
  const { calls } = tabsMock({ active: false });
  const result = await browsingContextCaptureScreenshot({ format: "jpeg", quality: 50 });
  assert.equal(result["lg:activated"], true);
  assert.deepEqual(calls[0], { update: [7, { active: true }] });
  assert.deepEqual(calls[2], { capture: [3, { format: "jpeg", quality: 50 }] });
  clearChrome();
});

test("a full-page screenshot is refused, not silently answered with the viewport", async () => {
  tabsMock();
  const err = await rejectsWith(
    () => browsingContextCaptureScreenshot({ origin: "document" }),
    "unsupported operation",
  );
  assert.match(err.message, /scroll-and-stitch/);
  clearChrome();
});

test("getTree lists only owned tabs by default, all: true for the whole browser", async () => {
  resetOwnership();
  ownWorld(
    [
      { id: 1, url: "https://a.test/", groupId: 500 },
      { id: 2, url: "https://b.test/" },
    ],
    { extra: { webNavigation: { getAllFrames: async () => [] } } },
  );
  const own = await browsingContextGetTree({});
  assert.deepEqual(own.contexts.map((c) => c.url), ["https://a.test/"]);
  const every = await browsingContextGetTree({ all: true });
  assert.deepEqual(every.contexts.map((c) => c.url), ["https://a.test/", "https://b.test/"]);
  clearChrome();
});

test("operating on an existing tab never moves it into a group", async () => {
  const { calls } = tabsMock();
  try {
    await browsingContextNavigate({ context: "7", url: "https://a.test/" });
    await browsingContextActivate({ context: "7" });
    await browsingContextReload({ context: "7" });
    assert.equal(calls.filter((call) => "group" in call).length, 0,
                 "用户自己的标签页被搬进了分组");
  } finally {
    clearChrome();
  }
});

test("create rebuilds the dedicated window+group when the remembered group is gone", async () => {
  resetOwnership();
  const calls: Any[] = [];
  installChrome({
    tabs: {
      query: async () => [],
      create: async (opts: Any) => {
        calls.push({ create: opts });
        return { id: 99, windowId: opts.windowId, url: opts.url };
      },
      group: async (opts: Any) => {
        calls.push({ group: opts });
        return opts.groupId ?? 11;
      },
    },
    tabGroups: {
      // session 里记的 500 已经死了（用户关组）：get 抛，query 空 → 走重建
      get: async (id: number) => {
        if (id === 500) throw new Error("No group with id: 500.");
        return { id, windowId: 21, title: "browse" };
      },
      query: async () => [],
      update: async (id: number, opts: Any) => ({ id, ...opts }),
    },
    windows: {
      create: async (opts: Any) => {
        calls.push({ windowCreate: opts });
        return { id: 21, tabs: [{ id: 901, url: "about:blank" }] };
      },
    },
    storage: {
      session: {
        get: async () => ({ "browse:own": { groupId: 500 } }),
        set: async () => {},
      },
      local: { get: async () => ({}), set: async () => {} },
    },
  });
  try {
    assert.deepEqual(await browsingContextCreate({ url: "https://x.test/" }), { context: "99" });
    // 重建：先建窗口、把新窗口的 tab 收成组，然后开的页面才进这个组
    assert.deepEqual(calls[0], { windowCreate: { focused: true } });
    assert.deepEqual(calls[1], { group: { tabIds: [901] } });
    assert.deepEqual(calls[3], { group: { tabIds: [99], groupId: 11 } });
  } finally {
    clearChrome();
  }
});
