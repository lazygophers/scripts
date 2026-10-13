import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import {
  netRouteAdd,
  netRuleArm,
  netRouteClear,
  netRouteList,
  netRouteRemove,
  netRuleForget,
  resetNetRules,
} from "../src/handlers/netrule.ts";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;
type Listener = (...args: Any[]) => void;

/** chrome.debugger 替身：记录 attach/detach、Fetch 域命令、事件监听可手动 fire。 */
function debuggerMock(opts: { failAttachFor?: number[] } = {}) {
  const state = {
    attached: new Set<number>(),
    commands: [] as Array<{ tabId: number; method: string; params?: Any }>,
    events: [] as Listener[],
    detaches: [] as Listener[],
  };
  const mock = {
    attach: async ({ tabId }: Any) => {
      if (opts.failAttachFor?.includes(tabId as number)) {
        throw new Error("Another debugger is already attached");
      }
      state.attached.add(tabId as number);
    },
    sendCommand: async ({ tabId }: Any, method: string, params?: Any) => {
      state.commands.push({ tabId: tabId as number, method, params });
      if (method === "Fetch.enable" && params === undefined) {
        // 测试默认不 mock Fetch.enable 的失败
      }
    },
    detach: async ({ tabId }: Any) => {
      state.attached.delete(tabId as number);
    },
    onEvent: {
      addListener: (fn: Listener) => state.events.push(fn),
      removeListener: (fn: Listener) => {
        const i = state.events.indexOf(fn);
        if (i >= 0) state.events.splice(i, 1);
      },
    },
    onDetach: {
      addListener: (fn: Listener) => state.detaches.push(fn),
      removeListener: (fn: Listener) => {
        const i = state.detaches.indexOf(fn);
        if (i >= 0) state.detaches.splice(i, 1);
      },
    },
  };
  return { mock, state };
}

function fire(state: { events: Listener[] }, tabId: number, method: string, params: Any): void {
  for (const fn of state.events) fn({ tabId }, method, params);
}

interface Fixture {
  dbg: ReturnType<typeof debuggerMock>;
  store: Record<string, unknown>;
  asked: ConfirmRequest[];
}

function setup(confirmOk = true): Fixture {
  const dbg = debuggerMock();
  const asked: ConfirmRequest[] = [];
  setConfirmHook(async (req) => {
    asked.push(req);
    return confirmOk;
  });
  const { store } = ownWorld(
    [{ id: 11, url: "https://example.test/a", active: true, groupId: 500 }],
    { extra: { debugger: dbg.mock } },
  );
  return { dbg, store, asked };
}

function paused(url: string): Any {
  return { requestId: "req-1", request: { url } };
}

afterEach(() => {
  resetNetRules();
  setConfirmHook(async () => true);
  clearChrome();
});

test("add mock rule asks confirm, attaches, and enables Fetch with the pattern", async () => {
  const { dbg, asked } = setup();
  const rule = await netRouteAdd({
    pattern: "https://api.test/v1/*",
    action: "mock",
    body: '{"ok":true}',
    status: 201,
    headers: [{ name: "content-type", value: "application/json" }],
  });
  assert.equal(rule.action, "mock");
  assert.match(rule.id, /^rule-\d+$/);
  assert.deepEqual(asked.map((a) => a.action), ["netRoute"]);
  assert.deepEqual([...dbg.state.attached], [11]);
  const enable = dbg.state.commands.find((c) => c.method === "Fetch.enable");
  assert.deepEqual(enable?.params, { patterns: [{ urlPattern: "https://api.test/v1/*" }] });
});

test("a matching request is fulfilled with the mocked response", async () => {
  const { dbg } = setup();
  await netRouteAdd({ pattern: "https://api.test/v1/*", action: "mock", body: "hello" });
  fire(dbg.state, 11, "Fetch.requestPaused", paused("https://api.test/v1/data"));
  await new Promise((r) => setTimeout(r, 0));
  const fulfill = dbg.state.commands.find((c) => c.method === "Fetch.fulfillRequest");
  assert.equal(fulfill?.params?.responseCode, 200);
  // UTF-8 的 "hello" → base64
  assert.equal(fulfill?.params?.body, btoa("hello"));
  assert.deepEqual(fulfill?.params?.responseHeaders,
    [{ name: "content-type", value: "text/plain; charset=utf-8" }]);
  const listed = await netRouteList();
  assert.equal(listed.rules[0]?.hits, 1);
});

test("an abort rule fails the request", async () => {
  const { dbg } = setup();
  await netRouteAdd({ pattern: "*tracker*", action: "abort" });
  fire(dbg.state, 11, "Fetch.requestPaused", paused("https://tracker.test/pixel.gif"));
  await new Promise((r) => setTimeout(r, 0));
  const fail = dbg.state.commands.find((c) => c.method === "Fetch.failRequest");
  assert.deepEqual(fail?.params, { requestId: "req-1", errorReason: "Aborted" });
});

test("a request paused by another pattern is continued, not hijacked", async () => {
  const { dbg } = setup();
  await netRouteAdd({ pattern: "https://api.test/v1/*", action: "abort" });
  fire(dbg.state, 11, "Fetch.requestPaused", paused("https://elsewhere.test/x"));
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(dbg.state.commands.some((c) => c.method === "Fetch.continueRequest"));
  assert.ok(!dbg.state.commands.some((c) => c.method === "Fetch.failRequest"));
});

test("removing the last rule disables Fetch and detaches", async () => {
  const { dbg } = setup();
  const rule = await netRouteAdd({ pattern: "*", action: "abort" });
  await netRouteRemove({ id: rule.id });
  assert.ok(dbg.state.commands.some((c) => c.method === "Fetch.disable"));
  assert.equal(dbg.state.attached.size, 0);
  await rejectsWith(() => netRouteRemove({ id: rule.id }), "invalid argument");
});

test("clear drops every rule", async () => {
  const { dbg } = setup();
  await netRouteAdd({ pattern: "a*", action: "abort" });
  await netRouteAdd({ pattern: "b*", action: "mock", body: "x" });
  const { removed } = await netRouteClear();
  assert.equal(removed, 2);
  assert.equal((await netRouteList()).rules.length, 0);
  assert.equal(dbg.state.attached.size, 0);
});

test("bad action / status / headers are rejected up front", async () => {
  setup();
  await rejectsWith(() => netRouteAdd({ pattern: "*", action: "redirect" }), "invalid argument");
  await rejectsWith(() => netRouteAdd({ pattern: "*", action: "mock", status: 99 }), "invalid argument");
  await rejectsWith(
    () => netRouteAdd({ pattern: "*", action: "mock", headers: [{ name: "x" }] }),
    "invalid argument",
  );
});

test("a refused confirm never attaches", async () => {
  const { dbg, asked } = setup(false);
  await rejectsWith(() => netRouteAdd({ pattern: "*", action: "abort" }), "lg:user rejected");
  assert.deepEqual(asked.map((a) => a.action), ["netRoute"]);
  assert.equal(dbg.state.attached.size, 0);
  assert.equal((await netRouteList()).rules.length, 0);
});

test("arm re-attaches persisted rules for owned tabs and drops dead ones", async () => {
  const { dbg, store } = setup();
  await netRouteAdd({ pattern: "*", action: "abort" });
  resetNetRules(); // 模拟服务重启：内存清空，storage 里的规则还在
  assert.equal((await netRouteList()).rules.length, 0);
  // tab 11 还在且归属 browse——重新武装
  await netRuleArm();
  assert.deepEqual([...dbg.state.attached], [11]);
  assert.equal((await netRouteList()).rules.length, 1);
  assert.ok(store["browse:netrules"]);
  // tab 关了：规则作废（persist 是 fire-and-forget，等一拍再 arm）
  netRuleForget(11);
  await new Promise((r) => setTimeout(r, 0));
  resetNetRules();
  await netRuleArm();
  assert.equal((await netRouteList()).rules.length, 0);
});
