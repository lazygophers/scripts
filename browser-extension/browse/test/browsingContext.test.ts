import assert from "node:assert/strict";
import test from "node:test";
import {
  browsingContextActivate,
  browsingContextCaptureScreenshot,
  browsingContextClose,
  browsingContextCreate,
  browsingContextNavigate,
  browsingContextReload,
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
        return { id: 99 };
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
