import assert from "node:assert/strict";
import test from "node:test";
import {
  commandsList,
  listenUi,
  omniboxSetDefault,
  sidePanelBehavior,
  sidePanelClose,
  sidePanelOpen,
} from "../src/handlers/ui.ts";
import { setEventSink } from "../src/events.ts";
import { clearContextCache } from "../src/handlers/context.ts";
import { installChrome, ownSession, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

function setup() {
  const calls: Any = { opened: [], closed: [], behavior: [], suggestions: [], listeners: {} };
  installChrome({
    ...ownSession(),
    commands: {
      getAll: async () => [{ name: "_execute_action" }, { name: "open-panel" }],
      onCommand: { addListener: (fn: Any) => { calls.listeners.command = fn; } },
    },
    sidePanel: {
      open: async (opts: Any) => { calls.opened.push(opts); },
      close: async (opts: Any) => { calls.closed.push(opts); },
      setPanelBehavior: async (opts: Any) => { calls.behavior.push(opts); },
    },
    omnibox: {
      setDefaultSuggestion: async (s: Any) => { calls.suggestions.push(s); },
      onInputStarted: { addListener: (fn: Any) => { calls.listeners.started = fn; } },
      onInputChanged: { addListener: (fn: Any) => { calls.listeners.changed = fn; } },
      onInputEntered: { addListener: (fn: Any) => { calls.listeners.entered = fn; } },
      onInputCancelled: { addListener: (fn: Any) => { calls.listeners.cancelled = fn; } },
    },
  });
  clearContextCache();
  return { calls };
}

test.afterEach(() => {
  setEventSink(null);
  clearContextCache();
});

test("commandsList returns chrome.commands.getAll", async () => {
  setup();
  assert.deepEqual(await commandsList(), {
    commands: [{ name: "_execute_action" }, { name: "open-panel" }],
  });
});

test("sidePanel open/close with and without a target", async () => {
  const { calls } = setup();
  assert.deepEqual(await sidePanelOpen({}), { opened: true });
  assert.deepEqual(await sidePanelClose({}), { closed: true });
  assert.deepEqual(calls.opened, [{}]);
  assert.deepEqual(calls.closed, [{}]);

  // 带目标：resolveContextOnce 走当前活动 tab（ownSession 的夹具是 tab 7）
  assert.deepEqual(await sidePanelOpen({ context: "7" }), { opened: true });
  assert.deepEqual(await sidePanelClose({ context: "7" }), { closed: true });
  assert.deepEqual(calls.opened.at(-1), { tabId: 7 });
  assert.deepEqual(calls.closed.at(-1), { tabId: 7 });
});

test("sidePanelBehavior sets the action-click behavior", async () => {
  const { calls } = setup();
  assert.deepEqual(await sidePanelBehavior({ openPanelOnActionClick: true }), {
    openPanelOnActionClick: true,
  });
  assert.deepEqual(await sidePanelBehavior({}), { openPanelOnActionClick: false });
  assert.deepEqual(calls.behavior, [
    { openPanelOnActionClick: true },
    { openPanelOnActionClick: false },
  ]);
});

test("omniboxSetDefault requires a description", async () => {
  const { calls } = setup();
  await rejectsWith(() => omniboxSetDefault({}), "invalid argument");
  assert.deepEqual(await omniboxSetDefault({ description: "搜: %s" }), { default: "搜: %s" });
  assert.deepEqual(calls.suggestions, [{ description: "搜: %s" }]);
});

test("listenUi forwards commands and omnibox phases as events", () => {
  const { calls } = setup();
  const seen: Any[] = [];
  setEventSink((event) => seen.push(event));
  listenUi();

  (calls.listeners.command as (c: string) => void)("open-panel");
  (calls.listeners.started as () => void)();
  (calls.listeners.changed as (t: string) => void)("query");
  (calls.listeners.entered as (t: string, d: string) => void)("goto https://a.test", "currentTab");
  (calls.listeners.cancelled as () => void)();

  assert.deepEqual(seen.map((e) => [e.method, e.params]), [
    ["lg:commands.triggered", { command: "open-panel" }],
    ["lg:omnibox.input", { phase: "started" }],
    ["lg:omnibox.input", { phase: "changed", text: "query" }],
    ["lg:omnibox.input", { phase: "entered", text: "goto https://a.test", disposition: "currentTab" }],
    ["lg:omnibox.input", { phase: "cancelled" }],
  ]);
});
