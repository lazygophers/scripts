import assert from "node:assert/strict";
import test from "node:test";
import {
  consoleForget,
  consoleNavigate,
  consoleRead,
  consoleReset,
  consoleStart,
  consoleStop,
  resetConsole,
} from "../src/handlers/console.ts";
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;
type Listener = (...args: Any[]) => void;

/** chrome.debugger 替身：记录 attach/detach 状态，事件监听可手动 fire。 */
function debuggerMock(opts: { failAttachFor?: number[]; failEnableFor?: number[] } = {}) {
  const state = {
    attached: new Set<number>(),
    enabled: new Set<number>(),
    events: [] as Listener[],
    detaches: [] as Listener[],
  };
  const listeners = (): Any => ({
    addListener: (fn: Listener) => state.events.push(fn),
    removeListener: (fn: Listener) => {
      const i = state.events.indexOf(fn);
      if (i >= 0) state.events.splice(i, 1);
    },
  });
  const detachListeners = (): Any => ({
    addListener: (fn: Listener) => state.detaches.push(fn),
    removeListener: (fn: Listener) => {
      const i = state.detaches.indexOf(fn);
      if (i >= 0) state.detaches.splice(i, 1);
    },
  });
  const mock = {
    attach: async ({ tabId }: Any) => {
      if (opts.failAttachFor?.includes(tabId as number)) {
        throw new Error("Another debugger is already attached");
      }
      state.attached.add(tabId as number);
    },
    sendCommand: async ({ tabId }: Any, method: string) => {
      if (method === "Runtime.enable") {
        if (opts.failEnableFor?.includes(tabId as number)) {
          throw new Error("enable failed");
        }
        state.enabled.add(tabId as number);
      }
    },
    detach: async ({ tabId }: Any) => {
      state.attached.delete(tabId as number);
    },
    onEvent: listeners(),
    onDetach: detachListeners(),
  };
  return { mock, state };
}

function fire(state: { events: Listener[] }, tabId: number, method: string, params: Any): void {
  for (const fn of state.events) fn({ tabId }, method, params);
}

function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

interface Harness {
  dbg: ReturnType<typeof debuggerMock>;
  store: Record<string, unknown>;
  tabs: Any[];
}

function setup(
  tabList: Any[] = [{ id: 11, url: "https://example.test/a", groupId: 500 }],
  dbgOpts: { failAttachFor?: number[]; failEnableFor?: number[] } = {},
): Harness {
  const dbg = debuggerMock(dbgOpts);
  const world = ownWorld(tabList, { extra: { debugger: dbg.mock } });
  return { dbg, store: world.store, tabs: world.tabs as Any[] };
}

function teardown(): void {
  resetConsole();
  clearChrome();
}

test("start attaches and enables Runtime; read returns captured entries; stop detaches", async () => {
  const h = setup();
  const started = await consoleStart({ context: "11" });
  assert.deepEqual(started, { context: "11", started: true });
  assert.ok(h.dbg.state.attached.has(11));
  assert.ok(h.dbg.state.enabled.has(11));

  fire(h.dbg.state, 11, "Runtime.consoleAPICalled", {
    type: "log",
    timestamp: 1000,
    args: [{ type: "string", value: "hello" }, { type: "number", value: 42 }],
  });
  await tick();
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, true);
  assert.equal(read.entries.length, 1);
  assert.equal(read.entries[0].level, "log");
  assert.equal(read.entries[0].text, "hello 42");

  const stopped = await consoleStop({ context: "11" });
  assert.deepEqual(stopped, { context: "11", stopped: true });
  assert.ok(!h.dbg.state.attached.has(11));
  const after = await consoleRead({ context: "11" });
  assert.equal(after.collecting, false);
  teardown();
});

test("exception events carry error level and stack", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  fire(h.dbg.state, 11, "Runtime.exceptionThrown", {
    timestamp: 2000,
    exceptionDetails: {
      text: "Uncaught",
      exception: { type: "object", subtype: "error", description: "TypeError: x is not a function" },
      stackTrace: { callFrames: [{ functionName: "boom", url: "https://example.test/a", lineNumber: 2, columnNumber: 3 }] },
    },
  });
  await tick();
  const read = await consoleRead({ context: "11", level: "error" });
  assert.equal(read.entries.length, 1);
  assert.equal(read.entries[0].level, "error");
  assert.equal(read.entries[0].text, "TypeError: x is not a function");
  assert.match(read.entries[0].stack ?? "", /boom@https:\/\/example\.test\/a:3:4/);
  teardown();
});

test("objects are rendered from CDP description, never evaluated", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  fire(h.dbg.state, 11, "Runtime.consoleAPICalled", {
    type: "log",
    args: [{ type: "object", className: "PasswordInput", description: "PasswordInput {}" }],
  });
  await tick();
  const read = await consoleRead({ context: "11" });
  assert.equal(read.entries[0].text, "PasswordInput {}");
  teardown();
});

test("sensitive text is redacted before entering the ring", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  fire(h.dbg.state, 11, "Runtime.consoleAPICalled", {
    type: "log",
    args: [{ type: "string", value: "token: ghp_" + "A".repeat(36) }],
  });
  await tick();
  const read = await consoleRead({ context: "11" });
  assert.match(read.entries[0].text, /\[REDACTED\]/);
  teardown();
});

test("start twice on the same tab is idempotent", async () => {
  setup();
  await consoleStart({ context: "11" });
  const again = await consoleStart({ context: "11" });
  assert.deepEqual(again, { context: "11", started: true });
  teardown();
});

// 归属拒绝在 dispatch 层（enforceOwnTab），handler 直调不做；事件路径的
// 撤权拦截由「revoking ownership mid-collection」覆盖。

test("attach failure surfaces as unsupported operation and leaves no session", async () => {
  setup(undefined, { failAttachFor: [11] });
  const err = await rejectsWith(() => consoleStart({ context: "11" }), "unsupported operation");
  assert.match(err.message, /attach the debugger/);
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, false);
  teardown();
});

test("Runtime.enable failure detaches and does not leave a half-open session", async () => {
  const h = setup(undefined, { failEnableFor: [11] });
  await rejectsWith(() => consoleStart({ context: "11" }), "unknown error");
  assert.ok(!h.dbg.state.attached.has(11));
  teardown();
});

test("limit and level validation", async () => {
  setup();
  await rejectsWith(() => consoleRead({ context: "11", limit: 0 }), "invalid argument");
  await rejectsWith(() => consoleRead({ context: "11", limit: 501 }), "invalid argument");
  await rejectsWith(() => consoleRead({ context: "11", limit: 1.5 }), "invalid argument");
  await rejectsWith(() => consoleRead({ context: "11", level: "debug" }), "invalid argument");
  teardown();
});

test("read before start is not an error (poll-friendly)", async () => {
  setup();
  const read = await consoleRead({ context: "11" });
  assert.deepEqual(read, { context: "11", collecting: false, entries: [], dropped: 0 });
  teardown();
});

test("ring eviction counts dropped entries", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  for (let i = 0; i < 505; i++) {
    fire(h.dbg.state, 11, "Runtime.consoleAPICalled", {
      type: "log",
      args: [{ type: "number", value: i }],
    });
  }
  await tick();
  const read = await consoleRead({ context: "11" });
  assert.equal(read.entries.length, 500);
  assert.equal(read.dropped, 5);
  teardown();
});

test("revoking ownership mid-collection drops the session and detaches", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  // 拖出 browse 组
  (h.tabs[0] as Any).groupId = 999;
  fire(h.dbg.state, 11, "Runtime.consoleAPICalled", { type: "log", args: [] });
  await tick();
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, false);
  assert.ok(!h.dbg.state.attached.has(11));
  teardown();
});

test("external detach clears the session", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  for (const fn of h.dbg.state.detaches) fn({ tabId: 11 }, "target_closed");
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, false);
  teardown();
});

test("navigation clears the ring but keeps collecting", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  fire(h.dbg.state, 11, "Runtime.consoleAPICalled", { type: "log", args: [{ type: "string", value: "old" }] });
  await tick();
  consoleNavigate(11);
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, true);
  assert.equal(read.entries.length, 0);
  teardown();
});

test("tab close forgets the session", async () => {
  const h = setup();
  await consoleStart({ context: "11" });
  consoleForget(11);
  const read = await consoleRead({ context: "11" });
  assert.equal(read.collecting, false);
  assert.ok(!h.dbg.state.attached.has(11));
  teardown();
});

test("MAX_TABS cap enforced", async () => {
  const tabs: Any[] = [];
  for (let i = 0; i < 11; i++) {
    tabs.push({ id: 100 + i, url: `https://example.test/${i}`, groupId: 500 });
  }
  const h = setup(tabs);
  for (let i = 0; i < 10; i++) {
    await consoleStart({ context: String(100 + i) });
  }
  await rejectsWith(() => consoleStart({ context: "110" }), "invalid argument");
  teardown();
});
