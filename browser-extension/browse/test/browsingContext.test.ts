test("create opens a tab in the current window; grouping is the CLI's job", async () => {
  const { calls } = tabsMock();
  assert.deepEqual(await browsingContextCreate({ url: "https://x.test/" }), {
    context: "99",
  });
  assert.deepEqual(calls[0], { create: { url: "https://x.test/", active: true } });
  assert.equal(calls.some((call) => "group" in call || "windowCreate" in call), false,
               "扩展不成组不建窗口；组名归 CLI 的 browse open --group");
  clearChrome();
});

test("create background opens without focusing", async () => {
  const { calls } = tabsMock();
  await browsingContextCreate({ url: "https://x.test/", background: true });
  assert.deepEqual(calls[0], { create: { url: "https://x.test/", active: false } });
  clearChrome();
});

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
      get: async () => ({ id: 500, windowId: 3, title: "browse/default" }),
      query: async (q: { title?: string }) =>
        (q?.title === undefined || q.title === "browse/default")
          ? [{ id: 500, windowId: 3, title: "browse/default" }]
          : [],
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

test("getTree: root 对不上时报 no such frame，没 id 的 tab 没有 children", async () => {
  tabsMock();
  await rejectsWith(() => browsingContextGetTree({ root: "999" }), "no such frame");

  // tabs.query 回一个没 id 的 tab：tabToContext 走 tabId=-1，frameChildren 直接空
  installChrome({
    tabs: {
      query: async () => [{ url: "https://weird.test/", active: true, groupId: 500 }],
      get: async () => ({}),
    },
    tabGroups: { query: async () => [{ id: 500, title: "browse/default" }] },
  });
  const tree = await browsingContextGetTree({});
  assert.equal(tree.contexts.length, 1);
  assert.equal(tree.contexts[0].context, "-1");
  assert.deepEqual(tree.contexts[0].children, []);
  clearChrome();
});

test("create: 开出来的 tab 没 id 直接报 unknown error", async () => {
  const calls: Any[] = [];
  installChrome({
    tabs: {
      query: async () => [],
      get: async () => ({}),
      create: async (opts: Any) => {
        calls.push(opts);
        return { windowId: 3 };
      },
    },
  });
  await rejectsWith(() => browsingContextCreate({ url: "https://x.test/" }), "unknown error");
  assert.equal(calls.length, 1);
  clearChrome();
});

test("captureScreenshot 只认 viewport 和 png/jpeg", async () => {
  const { chrome } = tabsMock();
  void chrome;
  await rejectsWith(
    () => browsingContextCaptureScreenshot({ origin: "document" }),
    "unsupported operation",
  );
  await rejectsWith(() => browsingContextCaptureScreenshot({ format: "webp" }), "invalid argument");
  clearChrome();
});

test("reload 的超时路径：页面一直不 complete 就按 unknown error 拒", async () => {
  const listeners: ((id: number, info: Any) => void)[] = [];
  installChrome({
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true, groupId: 500 }],
      get: async () => ({ id: 7, url: "https://a.test/", active: true, groupId: 500 }),
      reload: async () => {},
      onUpdated: {
        addListener: (fn: (id: number, info: Any) => void) => listeners.push(fn),
        removeListener: () => {},
      },
    },
  });
  await rejectsWith(
    () => browsingContextReload({ context: "7", timeout: 5 }),
    "unknown error",
  );
  assert.equal(listeners.length, 1, "监听器挂上了，只是没人触发");
  clearChrome();
});

test("frameChildren: chrome:// 页面取不到 frame 清单时按无子级上报", async () => {
  installChrome({
    tabs: {
      query: async () => [{ id: 7, url: "chrome://settings/", active: true, groupId: 500 }],
      get: async () => ({ id: 7, url: "chrome://settings/", active: true, groupId: 500 }),
    },
    tabGroups: { query: async () => [{ id: 500, title: "browse/default" }] },
    webNavigation: {
      getAllFrames: async () => {
        throw new Error("cannot access chrome://");
      },
    },
  });
  const tree = await browsingContextGetTree({});
  assert.deepEqual(tree.contexts[0].children, []);

  // 正常页面：子 frame 清单映射成 children
  installChrome({
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true, groupId: 500 }],
      get: async () => ({ id: 7, url: "https://a.test/", active: true, groupId: 500 }),
    },
    tabGroups: { query: async () => [{ id: 500, title: "browse/default" }] },
    webNavigation: {
      getAllFrames: async () => [
        { frameId: 0, parentFrameId: -1, url: "https://a.test/" },
        { frameId: 2, parentFrameId: 0, url: "https://ads.test/f" },
      ],
    },
  });
  const tree2 = await browsingContextGetTree({});
  assert.deepEqual(tree2.contexts[0].children, [{
    context: "7.2", parent: "7", url: "https://ads.test/f", children: [],
  }]);
  clearChrome();
});
