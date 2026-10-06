import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

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
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

afterEach(() => {
  clearChrome();
  clearContextCache();
  setEventSink(null);
});

function listenerWorld(extra: Any = {}): { events: Any[] } {
  const events: Any[] = [];
  setEventSink((e) => events.push(e));
  installChrome({
    commands: {
      getAll: async () => [{ name: "snap" }],
      onCommand: { addListener: (_fn: unknown) => {} },
    },
    omnibox: {
      setDefaultSuggestion: async () => {},
      onInputStarted: { addListener: (_fn: unknown) => {} },
      onInputChanged: { addListener: (_fn: unknown) => {} },
      onInputEntered: { addListener: (_fn: unknown) => {} },
      onInputCancelled: { addListener: (_fn: unknown) => {} },
    },
    ...extra,
  });
  return { events };
}

describe("ui.commands", () => {
  it("lists keyboard commands", async () => {
    listenerWorld();
    assert.deepEqual(await commandsList(), { commands: [{ name: "snap" }] });
  });

  it("refuses when the commands API is missing", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => commandsList(), "unsupported operation");
  });
});

describe("ui.sidePanel", () => {
  it("opens on the resolved tab when a target is given", async () => {
    ownWorld([{ id: 1, url: "https://a.example/x", groupId: 500, active: true }]);
    const opens: Any[] = [];
    listenerWorld({
      sidePanel: {
        open: async (o: Any) => void opens.push(o),
        close: async (o: Any) => void opens.push({ close: o }),
        setPanelBehavior: async () => {},
      },
    });
    assert.deepEqual(await sidePanelOpen({ context: "1" }), { opened: true });
    assert.deepEqual(opens[0], { tabId: 1 });
    assert.deepEqual(await sidePanelOpen({}), { opened: true });
    assert.deepEqual(opens[1], {});
    assert.deepEqual(await sidePanelClose({ context: "1" }), { closed: true });
    assert.deepEqual(await sidePanelClose({}), { closed: true });
    assert.deepEqual(await sidePanelBehavior({ openPanelOnActionClick: true }), {
      openPanelOnActionClick: true,
    });
    assert.deepEqual(await sidePanelBehavior({}), { openPanelOnActionClick: false });
  });

  it("refuses when the sidePanel API is missing", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => sidePanelOpen({}), "unsupported operation");
    await rejectsWith(() => sidePanelClose({}), "unsupported operation");
    await rejectsWith(() => sidePanelBehavior({}), "unsupported operation");
  });
});

describe("ui.omnibox", () => {
  it("sets the default suggestion", async () => {
    listenerWorld();
    assert.deepEqual(await omniboxSetDefault({ description: "搜: %s" }), { default: "搜: %s" });
  });

  it("requires a description", async () => {
    listenerWorld();
    await rejectsWith(() => omniboxSetDefault({}), "invalid argument");
  });
});

describe("ui.listenUi", () => {
  it("forwards command and omnibox events", () => {
    const { events } = listenerWorld();
    let onCommand: ((command: string) => void) | undefined;
    let onStarted: (() => void) | undefined;
    let onChanged: ((text: string) => void) | undefined;
    let onEntered: ((text: string, d: string) => void) | undefined;
    let onCancelled: (() => void) | undefined;
    installChrome({
      commands: {
        onCommand: {
          addListener: (fn: (c: string) => void) => (onCommand = fn),
        },
      },
      omnibox: {
        onInputStarted: { addListener: (fn: () => void) => (onStarted = fn) },
        onInputChanged: { addListener: (fn: (t: string) => void) => (onChanged = fn) },
        onInputEntered: { addListener: (fn: (t: string, d: string) => void) => (onEntered = fn) },
        onInputCancelled: { addListener: (fn: () => void) => (onCancelled = fn) },
      },
    });
    listenUi();
    onCommand?.("snap");
    onStarted?.();
    onChanged?.("q");
    onEntered?.("q", "newForegroundTab");
    onCancelled?.();
    assert.deepEqual(events, [
      { type: "event", method: "lg:commands.triggered", params: { command: "snap" } },
      { type: "event", method: "lg:omnibox.input", params: { phase: "started" } },
      { type: "event", method: "lg:omnibox.input", params: { phase: "changed", text: "q" } },
      {
        type: "event",
        method: "lg:omnibox.input",
        params: { phase: "entered", text: "q", disposition: "newForegroundTab" },
      },
      { type: "event", method: "lg:omnibox.input", params: { phase: "cancelled" } },
    ]);
  });

  it("tolerates missing command and omnibox APIs", () => {
    installChrome({});
    assert.doesNotThrow(() => listenUi());
  });
});
