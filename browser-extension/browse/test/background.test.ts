/**
 * service worker 入口。background.ts 没有导出：import 即注册全部监听、建连接、
 * 画徽章。所以这里只能驱动 —— 假 WebSocket（借 native-port.test.ts 的做法）、
 * 内存 storage，然后把注册进 mock 的监听器逐个捞出来喂消息，量的是接线本身：
 *
 * - 五类 onMessage 各自的应答形态（同步回 / 异步回 / 不回）
 * - 徽章的三种状态（未连 / 已连 / 有指令在跑）
 * - bridgeReport 对「连不上」「回错」「字段缺失」的容忍
 */
import assert from "node:assert/strict";
import test, { after } from "node:test";
import { emitEvent } from "../src/events.ts";
import { clearChrome, installChrome, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;
const g = globalThis as Any;

function captor(): { fns: ((...args: any[]) => unknown)[]; addListener: (fn: never) => void } {
  const fns: ((...args: any[]) => unknown)[] = [];
  return {
    fns,
    addListener: (fn: never) => void fns.push(fn as (...args: any[]) => unknown),
  };
}

async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const sockets: FakeSocket[] = [];
const badgeTexts: Any[] = [];
const badgeTitles: Any[] = [];
const alarmCreates: [string, Any][] = [];

class FakeSocket {
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    sockets.push(this);
  }
  send(text: string): void {
    this.sent.push(text);
  }
  close(): void {
    this.readyState = 0;
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  frame(message: unknown): void {
    this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
  }
  reply(id: number, reply: Any): void {
    this.frame({ id, ...reply });
  }
}

// ---- 全局桩：WebSocket 必须在 import background 之前就位（模块顶部就 connect）

const previousWebSocket = g.WebSocket;
g.WebSocket = FakeSocket;

storageMock();
const onMessage = captor();
const onStartup = captor();
const onInstalled = captor();
const windowsRemoved = captor();
const tabsUpdated = captor();
const tabsRemoved = captor();
const onAlarm = captor();

installChrome({
  ...((g.chrome as Any) ?? {}),
  action: {
    setBadgeBackgroundColor: async () => {},
    setBadgeText: (o: Any) => void badgeTexts.push(o),
    setTitle: (o: Any) => void badgeTitles.push(o),
  },
  runtime: {
    onMessage,
    onStartup,
    onInstalled,
    getURL: (path: string) => `chrome-extension://test-id/${path}`,
  },
  windows: {
    onRemoved: windowsRemoved,
    create: async () => ({ id: 77 }),
    remove: async () => {},
  },
  tabs: { onUpdated: tabsUpdated, onRemoved: tabsRemoved },
  alarms: {
    create: (name: string, info: Any) => void alarmCreates.push([name, info]),
    onAlarm,
  },
  commands: { onCommand: captor() },
  omnibox: {
    onInputStarted: captor(),
    onInputChanged: captor(),
    onInputEntered: captor(),
    onInputCancelled: captor(),
  },
  gcm: { onMessage: captor() },
  notifications: { onClicked: captor() },
  printerProvider: { onPrintRequested: captor() },
  webAuthenticationProxy: { onRequest: captor() },
});

await import("../src/background.ts");
await settle();

/** 驱动 onMessage 监听器，收集 sendResponse 的应答。 */
function send(message: unknown): { respond: Any[]; result: unknown } {
  const respond: Any[] = [];
  const result = onMessage.fns[0]?.(message, {}, (reply: unknown) => void respond.push(reply));
  return { respond, result };
}

/** 发 browse-bridge 并把 socket 上的待回请求答掉。 */
async function askBridge(
  answer: (id: number, method: string, sock: FakeSocket) => void,
  message: Any = {},
): Promise<Any[]> {
  const sock = sockets[sockets.length - 1]!;
  const before = sock.sent.length;
  const { respond } = send({ type: "browse-bridge", ...message });
  await settle();
  const pending = sock.sent.slice(before).map((raw) => JSON.parse(raw) as { id: number; method: string });
  for (const { id, method } of pending) {
    answer(id, method, sock);
  }
  await settle();
  return respond;
}

test("boot wires every listener, arms the alarm and paints the idle badge", () => {
  assert.ok(onMessage.fns.length === 1);
  assert.ok(onStartup.fns.length === 1);
  assert.ok(onInstalled.fns.length === 1);
  assert.ok(windowsRemoved.fns.length === 1);
  assert.ok(tabsUpdated.fns.length === 1);
  assert.ok(tabsRemoved.fns.length === 1);
  assert.ok(onAlarm.fns.length === 1);
  assert.deepEqual(alarmCreates, [["reconnect", { periodInMinutes: 0.5 }]]);
  // 模块顶部的 connect() 已经建了 socket，还没打开（readyState CONNECTING）
  assert.equal(sockets.length, 1);
  assert.equal(sockets[0]!.readyState, 0);
  assert.deepEqual(badgeTexts.at(-1), { text: "" });
  assert.deepEqual(badgeTitles.at(-1), { title: "browse: bridge not connected" });
});

test("browse-bridge degrades to connection status while the bridge is down", async () => {
  const { respond, result } = send({ type: "browse-bridge" });
  assert.equal(result, true, "异步应答必须 return true");
  await settle();
  const reply = respond[0] as Any;
  assert.equal(reply.ok, false);
  assert.equal(reply.error, "bridge not connected");
  assert.equal((reply.status as Any).url, "ws://127.0.0.1:9330");
  assert.equal((reply.status as Any).state, "disconnected");
});

test("browse-confirm, browse-log, browse-pick, unknown and null all answer in line", () => {
  const confirm = send({ type: "browse-confirm", token: "nobody", approved: true });
  assert.deepEqual(confirm.respond, [{ ok: false }], "没人等的 token 回 false");
  assert.equal(confirm.result, false);

  const log = send({ type: "browse-log" });
  assert.deepEqual(log.respond, [{ ok: true, entries: [], state: "disconnected" }]);
  assert.equal(log.result, false);

  const pick = send({ type: "browse-pick", streamId: "sid" });
  assert.deepEqual(pick.respond, [{ ok: false }], "没有等待中的选源窗回 false");
  assert.equal(pick.result, false);

  const unknown = send({ type: "zzz" });
  assert.deepEqual(unknown.respond, [], "不认识的消息不回应");
  assert.equal(unknown.result, false);

  const nothing = send(null);
  assert.deepEqual(nothing.respond, []);
  assert.equal(nothing.result, false);
});

test("opening the bridge flips the badge to connected and says hello", async () => {
  sockets[0]!.open();
  await settle();
  const hello = JSON.parse(sockets[0]!.sent[0] ?? "{}") as Any;
  assert.equal(hello.type, "hello");
  assert.equal(hello.role, "extension");
  assert.equal(typeof hello.instanceId, "string");
  assert.deepEqual(badgeTexts.at(-1), { text: "●" });
  assert.deepEqual(badgeTitles.at(-1), { title: "browse: connected" });
});

test("browse-bridge returns bridge info and its own instanceId when it answers", async () => {
  const reply = (
    await askBridge((id, _method, sock) => {
      sock.reply(id, { type: "success", result: { version: 1 } });
    }, { limit: 5 })
  )[0] as Any;
  assert.equal(reply.ok, true);
  assert.deepEqual(reply.info, { version: 1 });
  assert.equal(typeof reply.ownInstanceId, "string");
  assert.equal((reply.status as Any).state, "connected");
});

test("browse-bridge surfaces an error reply as ok:false", async () => {
  const reply = (
    await askBridge((id, _method, sock) => {
      sock.reply(id, { type: "error", error: "lg:browser not connected", message: "daemon gone" });
    })
  )[0] as Any;
  assert.equal(reply.ok, false);
  assert.equal(reply.error, "daemon gone");
  assert.equal(reply.info, undefined);
});

test("a running command paints the running badge and lands in the panel log", async () => {
  sockets[0]!.frame({ id: 7, method: "lg:cache.list", params: {} });
  await settle();
  const sent = JSON.parse(sockets[0]!.sent.at(-1) ?? "{}") as Any;
  assert.equal(sent.type, "success", "指令的应答回给了 bridge");
  assert.equal(sent.id, 7);
  assert.deepEqual(badgeTexts, [{ text: "" }, { text: "●" }, { text: "1" }, { text: "●" }]);
  assert.deepEqual(badgeTitles.at(-2), { title: "browse: connected, 1 running" });

  const log = send({ type: "browse-log" }).respond[0] as Any;
  assert.equal((log.entries as Any[]).length, 1);
  assert.equal((log.entries as Any[])[0]!.ok, true);
  assert.equal(log.state, "connected");
});

test("non-string and non-JSON socket messages are dropped", () => {
  assert.doesNotThrow(() => sockets[0]!.frame("{{{ not json"));
  assert.doesNotThrow(() => sockets[0]!.onmessage?.({ data: 42 }));
  // network.* 推送走 events.ts 的 sink（background 在启动时把 connection 递进去）
  emitEvent("lg:omnibox.input", { phase: "started" });
});

test("a dropped socket fails the pending panel query and schedules one retry", async () => {
  const pending = send({ type: "browse-bridge", limit: 2 });
  sockets[0]!.onclose?.(); // 桥断了：待回的请求必须被落地，不能挂着
  await settle();
  const reply = pending.respond[0] as Any;
  assert.equal(reply.ok, false);
  assert.equal(reply.error, "bridge 连接断开");
  assert.deepEqual(badgeTexts.at(-1), { text: "" });
  assert.deepEqual(badgeTitles.at(-1), { title: "browse: bridge not connected" });

  sockets[0]!.onclose?.(); // 已换手（socket 置空）的旧连接再断一次不算数
  assert.deepEqual(badgeTitles.at(-1), { title: "browse: bridge not connected" }); // 没再画新的
});

test("tab, window and alarm listeners forward to the cache and the reconnect", async () => {
  await tabsUpdated.fns[0]?.(1, { status: "loading" });
  tabsUpdated.fns[0]?.(1, { status: "complete" }); // 非 loading 不清缓存
  tabsRemoved.fns[0]?.(2);
  windowsRemoved.fns[0]?.(999); // 没有等待中的弹窗：no-op

  const before = sockets.length;
  onAlarm.fns[0]?.({ name: "other" }); // 别的闹钟不重连
  assert.equal(sockets.length, before);
  onAlarm.fns[0]?.({ name: "reconnect" }); // socket 已随断线清掉 → 真的重连
  await settle();
  assert.equal(sockets.length, before + 1);
});

test("onStartup resumes and onInstalled reconnects without extra sockets", async () => {
  const before = sockets.length;
  onStartup.fns[0]?.();
  await settle();
  assert.equal(sockets.length, before, "resume 对活着的 socket 不重建");
  onInstalled.fns[0]?.();
  await settle();
  assert.equal(sockets.length, before);
});

test("browse-disconnect brakes and the armed reconnect never fires", async () => {
  const { respond } = send({ type: "browse-disconnect" });
  assert.deepEqual(respond, [{ ok: true }]);
  assert.deepEqual(badgeTitles.at(-1), { title: "browse: bridge not connected" });
  onAlarm.fns[0]?.({ name: "reconnect" }); // 刹车后闹钟叫不醒
  await new Promise((resolve) => setTimeout(resolve, 700)); // 退避上限 500ms：等它落地
  assert.equal(sockets.length, 2, "刹车后不再建新 socket");
});

after(() => {
  g.WebSocket = previousWebSocket;
  clearChrome();
});
