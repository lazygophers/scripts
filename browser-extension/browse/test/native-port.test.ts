import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { NativeConnection } from "../src/native-port.ts";
import { clearChrome, installChrome, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

/**
 * 停止（stopped）状态的单一所有权：用户刹车后跨 service worker 重启保持，
 * alarm 兜底重连尊重它，浏览器完整启动（resume）才解除。
 * 2026-09-21 之前 background.ts 还有一个并行的 braked，SW 一被杀两者同时
 * 失忆；开头的两个测试就是钉住「不再有第二个状态源」。
 */

const sockets: FakeSocket[] = [];
let previousWebSocket: unknown;

class FakeSocket {
  static OPEN = 1;
  static throwNext = false;
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    if (FakeSocket.throwNext) {
      FakeSocket.throwNext = false;
      throw new Error("bridge unreachable");
    }
    sockets.push(this);
  }

  send(text: string): void {
    this.sent.push(text);
  }

  close(): void {
    this.readyState = 0;
  }

  /** 测试里把连接「打开」。 */
  open(): void {
    this.readyState = 1; // WebSocket.OPEN
    this.onopen?.({});
  }

  frame(message: unknown): void {
    this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
  }
}

/** 一次测试的固定环境：假 WebSocket + 内存 storage（跨「SW 重启」共享）。 */
function boot(): void {
  previousWebSocket = (globalThis as Any).WebSocket;
  (globalThis as Any).WebSocket = FakeSocket;
  installChrome({
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
      setTitle: async () => {},
    },
  });
  storageMock();
}

afterEach(() => {
  if (previousWebSocket !== undefined) {
    (globalThis as Any).WebSocket = previousWebSocket;
    previousWebSocket = undefined;
  }
  sockets.length = 0;
  clearChrome();
});

/** 等连接的异步尾巴（initDone / connect 链）跑完。 */
async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("braking persists: a restarted service worker does not reconnect", async () => {
  boot();
  const first = new NativeConnection(() => {}, () => {});
  first.connect();
  await settle();
  assert.equal(sockets.length, 1, "正常连接应该建 socket");
  first.disconnect(); // 用户刹车
  await settle();

  // 模拟 SW 被杀后重启：新的 NativeConnection，同一份 storage
  const second = new NativeConnection(() => {}, () => {});
  second.connect(); // 模块顶部的 connect() / 30 秒 alarm 都走这里
  second.connect(); // alarm 再叫一次也一样
  await settle();
  assert.equal(sockets.length, 1, "刹车后的重连必须被挡住");
  assert.equal(second.status().stopped, true);
});

test("resume() clears the brake and reconnects", async () => {
  boot();
  const first = new NativeConnection(() => {}, () => {});
  first.connect();
  await settle();
  first.disconnect();
  await settle();

  const second = new NativeConnection(() => {}, () => {});
  second.resume(); // 浏览器完整启动
  await settle();
  assert.equal(second.status().stopped, false);
  assert.equal(sockets.length, 2, "resume 后应该重新建 socket");
});

test("open sends a hello with browser and instance id", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  await settle();
  const hello = JSON.parse(sock.sent[0] ?? "{}");
  assert.equal(hello.type, "hello");
  assert.equal(hello.role, "extension");
  assert.equal(typeof hello.instanceId, "string");
  assert.ok(
    ["chrome", "edge", "brave", "opera", "vivaldi", "arc", "chromium"].includes(hello.browser),
    hello.browser,
  );
  // hello-ack 只是确认，不当指令处理也不当回包
  sock.frame({ type: "hello-ack" });
  assert.equal(conn.status().state, "connected");
  conn.disconnect();
});

test("send only goes out on an open socket", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.send({ type: "event", method: "x", params: {} }); // 没 socket：静默丢弃
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  conn.send({ type: "event", method: "x", params: {} });
  assert.deepEqual(JSON.parse(sock.sent.at(-1) ?? "{}"), { type: "event", method: "x", params: {} });
  conn.disconnect();
});

test("request resolves on success and rejects on an error reply", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();

  const ok = conn.request("lg:approvals.list");
  const sent = JSON.parse(sock.sent.at(-1) ?? "{}");
  assert.equal(sent.method, "lg:approvals.list");
  sock.frame({ type: "success", id: sent.id, result: ["a.test"] });
  assert.deepEqual(await ok, ["a.test"]);

  const bad = conn.request("lg:approvals.add", { domain: "x" });
  const sent2 = JSON.parse(sock.sent.at(-1) ?? "{}");
  sock.frame({ type: "error", id: sent2.id, error: "invalid argument", message: "no" });
  await assert.rejects(bad, /no/);

  conn.disconnect();
  await assert.rejects(conn.request("x"), /bridge not connected/);
});

test("commands dispatch: success and error replies both go back", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();

  sock.frame({ id: 7, method: "lg:cache.list", params: {} });
  await settle();
  const okReply = JSON.parse(sock.sent.at(-1) ?? "{}");
  assert.equal(okReply.type, "success");
  assert.equal(okReply.id, 7);
  assert.deepEqual(okReply.result, { pages: [] });

  sock.frame({ id: 8, method: "nonsense", params: {} });
  await settle();
  const errReply = JSON.parse(sock.sent.at(-1) ?? "{}");
  assert.equal(errReply.type, "error");
  assert.equal(errReply.id, 8);
  assert.equal(errReply.error, "unknown command");

  const log = conn.recent();
  assert.equal(log.length, 2);
  assert.equal(log[0]?.ok, false, "最新的在前");
  assert.equal(log[1]?.ok, true);
  conn.disconnect();
});

test("non-command, non-reply, non-JSON and binary messages are dropped", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  sock.frame({ type: "event", method: "whatever" }); // 不是指令也不是回包
  sock.frame("not json {{{");
  sock.frame(Buffer.from("binary")); // 非 string data
  await settle();
  assert.deepEqual(conn.recent(), []);
  conn.disconnect();
});

test("a dropped socket schedules a reconnect and fails pending requests", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  const pending = conn.request("lg:approvals.list");
  // 先挂上拒绝断言再断开，否则 reject 和 await 之间的空档算 unhandledRejection
  const failing = assert.rejects(pending, /bridge 连接断开/);
  sock.onclose?.();
  await settle();
  await failing;
  assert.equal(conn.status().state, "disconnected");
  assert.ok(conn.status().attempt >= 1);
  assert.ok(conn.status().reason.length > 0);
  conn.disconnect(); // 停掉退避定时器，免得泄漏进别的用例
});

test("a refusing WebSocket constructor counts as an unavailable bridge", async () => {
  boot();
  FakeSocket.throwNext = true;
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  assert.equal(sockets.length, 0);
  assert.equal(conn.status().state, "disconnected");
  assert.match(conn.status().reason, /bridge unreachable/);
  conn.disconnect();
});

test("browserName maps known brands and falls back to chromium", async () => {
  boot();
  const nav = globalThis.navigator as unknown as {
    userAgentData?: { brands: { brand: string }[] };
    userAgent?: string;
  };
  const cases: [{ brand: string }[], string][] = [
    [[{ brand: "Google Chrome" }, { brand: "other" }], "chrome"],
    [[{ brand: "Microsoft Edge" }], "edge"],
    [[{ brand: "Brave" }], "brave"],
    [[{ brand: "Opera" }], "opera"],
    [[{ brand: "Vivaldi" }], "vivaldi"],
    [[{ brand: "Arc" }], "arc"],
    [[{ brand: "Something Else" }], "chromium"],
  ];
  for (const [brands, want] of cases) {
    Object.defineProperty(globalThis.navigator, "userAgentData", {
      configurable: true,
      value: { brands },
    });
    // Arc 走的是 UA 兜底分支（brands 里不报自己），先清干净 UA 隔离用例
    Object.defineProperty(globalThis.navigator, "userAgent", {
      configurable: true,
      value: "Mozilla/5.0",
    });
    const conn = new NativeConnection(() => {}, () => {});
    conn.resume(); // 前一轮 disconnect 的刹车在同一份 storage 里，用 resume 解开
    await settle();
    const sock = sockets[0]!;
    sock.open();
    await settle();
    assert.equal(JSON.parse(sock.sent[0] ?? "{}").browser, want, brands[0]?.brand);
    conn.disconnect();
    sockets.length = 0;
  }
  // UA 兜底：brands 不报 Arc、UA 带 " Arc/<版本>" —— 真实 Arc 的形状
  Object.defineProperty(globalThis.navigator, "userAgent", {
    configurable: true,
    value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Arc/1.100.0",
  });
  const arcConn = new NativeConnection(() => {}, () => {});
  arcConn.resume();
  await settle();
  const arcSock = sockets[0]!;
  arcSock.open();
  await settle();
  assert.equal(JSON.parse(arcSock.sent[0] ?? "{}").browser, "arc");
  arcConn.disconnect();
  sockets.length = 0;
  Object.defineProperty(globalThis.navigator, "userAgent", {
    configurable: true,
    value: "Mozilla/5.0",
  });
  Object.defineProperty(globalThis.navigator, "userAgentData", {
    configurable: true,
    value: undefined,
  });
});

test("classify falls back to unknown error and describe handles odd shapes", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  // 一个抛非 Error 的 handler 不存在；这里直接喂一个 dispatch 会抛普通字符串的场景
  sock.frame({ id: 9, method: "lg:cache.get", params: {} });
  await settle();
  const reply = JSON.parse(sock.sent.at(-1) ?? "{}");
  assert.equal(reply.type, "error");
  assert.equal(reply.id, 9);
  conn.disconnect();
});

test("instance id falls back to a fresh uuid when storage is broken", async () => {
  boot();
  (globalThis as Any).chrome.storage.local.get = async () => {
    throw new Error("gone");
  };
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  await settle();
  const hello = JSON.parse(sock.sent[0] ?? "{}");
  assert.equal(typeof hello.instanceId, "string");
  conn.disconnect();
});

test("connect while already connected keeps the single socket", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  sockets[0]!.open();
  conn.connect(); // alarm 再叫也不重建
  await settle();
  assert.equal(sockets.length, 1);
  conn.disconnect();
});

test("the panel log keeps only the newest 20 commands", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const sock = sockets[0]!;
  sock.open();
  for (let i = 0; i < 25; i++) {
    sock.frame({ id: 100 + i, method: "lg:cache.list", params: {} });
  }
  await settle();
  assert.equal(conn.recent().length, 20);
  conn.disconnect();
});
