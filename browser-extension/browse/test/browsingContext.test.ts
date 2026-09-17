import assert from "node:assert/strict";
import test from "node:test";
import {
  browsingContextActivate,
  browsingContextCaptureScreenshot,
  browsingContextClose,
  browsingContextCreate,
  browsingContextNavigate,
  browsingContextReload,
  forgetGroups,
} from "../src/handlers/browsingContext.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

function tabsMock(tab: Any = {}): { calls: Any[]; chrome: Any } {
  const calls: Any[] = [];
  const state = { id: 7, url: "https://a.test/", active: true, windowId: 3, ...tab };
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
      update: async (id: number, opts: Any) => {
        calls.push({ groupUpdate: [id, opts] });
        return { id, ...opts };
      },
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

test("create opens a tab and returns its context id", async () => {
  const { calls } = tabsMock();
  assert.deepEqual(await browsingContextCreate({ url: "https://x.test/" }), {
    context: "99",
  });
  assert.deepEqual(calls[0], { create: { url: "https://x.test/", active: true } });
  clearChrome();
});

test("create type=window goes through windows.create and background unfocuses", async () => {
  const { calls } = tabsMock();
  assert.deepEqual(await browsingContextCreate({ type: "window", background: true }), {
    context: "42",
  });
  assert.deepEqual(calls[0], { windowCreate: { focused: false } });
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

test("a tab browse opened lands in its own group, so one click closes them all", async () => {
  forgetGroups();
  const { calls } = tabsMock();
  try {
    await browsingContextCreate({ url: "https://a.test/" });
    assert.deepEqual(calls[1], { group: { tabIds: [99] } });
    // 第一次建组时给它起名上色，用户在标签栏上认得出这是谁开的
    assert.deepEqual(calls[2], { groupUpdate: [11, { title: "browse", color: "blue" }] });

    await browsingContextCreate({ url: "https://b.test/" });
    // 第二个标签页进同一组，而不是每次新开一组
    assert.deepEqual(calls[4], { group: { tabIds: [99], groupId: 11 } });
    assert.equal(calls.filter((call) => "groupUpdate" in call).length, 1);
  } finally {
    clearChrome();
  }
});

test("operating on an existing tab never moves it into a group", async () => {
  forgetGroups();
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

test("a group the user has closed is forgotten, and the next tab starts a fresh one", async () => {
  forgetGroups();
  const calls: Any[] = [];
  let alive = true;
  installChrome({
    tabs: {
      create: async () => ({ id: 99, windowId: 3 }),
      group: async (opts: Any) => {
        calls.push({ group: opts });
        if (opts.groupId !== undefined && !alive) {
          // 用户把整组关掉之后，Chrome 就是这么报的
          throw new Error(`No group with id: ${String(opts.groupId)}.`);
        }
        return (opts.groupId as number | undefined) ?? 11;
      },
    },
    tabGroups: { update: async () => ({}) },
  });
  try {
    await browsingContextCreate({});
    alive = false;
    // 这一个分不进去了，但标签页照样开出来 —— 分组失败不该让 create 失败
    assert.deepEqual(await browsingContextCreate({}), { context: "99" });
    assert.deepEqual(calls[1], { group: { tabIds: [99], groupId: 11 } });
    // 忘掉那一组之后，下一个标签页重新建一组，而不是一直撞同一个死 id
    alive = true;
    await browsingContextCreate({});
    assert.deepEqual(calls[2], { group: { tabIds: [99] } });
  } finally {
    clearChrome();
  }
});

test("without tabs.group the tab still opens — grouping is a nicety, not the job", async () => {
  forgetGroups();
  const calls: Any[] = [];
  installChrome({
    tabs: {
      create: async (opts: Any) => {
        calls.push({ create: opts });
        return { id: 99, windowId: 3 };
      },
    },
  });
  try {
    assert.deepEqual(await browsingContextCreate({ url: "https://a.test/" }), { context: "99" });
    assert.equal(calls.length, 1);
  } finally {
    clearChrome();
  }
});
