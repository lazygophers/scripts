import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { NativeConnection } from "../src/native-port.ts";
import { clearChrome, installChrome } from "./mock.ts";

type Any = Record<string, unknown>;

/** Every connection a test opened. The 20s keepalive interval and the
 * reconnect timer both keep node alive, so each one must be shut down. */
const open: NativeConnection[] = [];

/**
 * A fake bridge WebSocket under test control: whatever the extension sends lands
 * in `sent`, and `reply` plays the bridge's answer back. `drop` simulates a cut
 * connection. 2026-09-15 起传输是 WebSocket（不再是 connectNative）。
 */
const sockets: FakeSocket[] = [];
let previousWebSocket: unknown;

class FakeSocket {
  static OPEN = 1;
  static throwNext = false;
  sent: Any[] = [];
  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  url: string;

  constructor(url: string) {
    this.url = url;
    if (FakeSocket.throwNext) {
      FakeSocket.throwNext = false;
      throw new Error("bridge unreachable");
    }
    sockets.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.readyState = 0;
  }

  /** Test-side: the socket is established. */
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  /** Test-side: the bridge says something. */
  reply(message: Any): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** Test-side: the connection is cut. */
  drop(): void {
    this.readyState = 0;
    this.onclose?.();
  }
}

function port(): {
  connection: NativeConnection;
  sent: Any[];
  reply: (message: Any) => void;
  drop: () => void;
} {
  previousWebSocket = (globalThis as Any).WebSocket;
  (globalThis as Any).WebSocket = FakeSocket;
  installChrome({
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
      setTitle: async () => {},
    },
  });
  const connection = new NativeConnection(
    () => {},
    () => {},
  );
  open.push(connection);
  connection.connect();
  const socket = sockets.at(-1)!;
  socket.open();
  return {
    connection,
    sent: socket.sent,
    reply: (message) => socket.reply(message),
    drop: () => socket.drop(),
  };
}

afterEach(() => {
  while (open.length) {
    open.pop()?.disconnect();
  }
  if (previousWebSocket !== undefined) {
    (globalThis as Any).WebSocket = previousWebSocket;
    previousWebSocket = undefined;
  }
  sockets.length = 0;
  clearChrome();
});

test("request sends a Command and resolves with the daemon's result", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.list");
  assert.deepEqual(p.sent.at(-1), {
    id: 1,
    method: "lg:approvals.list",
    params: {},
  });
  p.reply({ type: "success", id: 1, result: { domains: ["bank.test"] } });
  assert.deepEqual(await answer, { domains: ["bank.test"] });
});

test("two requests in flight come back to the right caller", async () => {
  const p = port();
  const first = p.connection.request("lg:approvals.approve", { domain: "a.test" });
  const second = p.connection.request("lg:approvals.revoke", { domain: "b.test" });
  // Answered out of order on purpose: matching is by id, not arrival.
  p.reply({ type: "success", id: 2, result: { domains: ["a.test"] } });
  p.reply({ type: "success", id: 1, result: { domains: ["a.test", "b.test"] } });
  assert.deepEqual(await first, { domains: ["a.test", "b.test"] });
  assert.deepEqual(await second, { domains: ["a.test"] });
});

test("an error reply rejects with the daemon's code", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.approve", {});
  p.reply({ type: "error", id: 1, error: "invalid argument", message: "要给一个 domain" });
  await assert.rejects(answer, (err: Error & { code?: string }) => {
    assert.equal(err.code, "invalid argument");
    assert.equal(err.message, "要给一个 domain");
    return true;
  });
});

test("a dropped port rejects everything in flight instead of hanging the panel", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.list");
  p.drop();
  await assert.rejects(answer, (err: Error & { code?: string }) => {
    assert.equal(err.code, "lg:browser not connected");
    return true;
  });
});

test("requesting with no bridge at all rejects rather than silently dropping", async () => {
  previousWebSocket = (globalThis as Any).WebSocket;
  (globalThis as Any).WebSocket = FakeSocket;
  FakeSocket.throwNext = true;
  const connection = new NativeConnection(() => {}, () => {});
  open.push(connection);
  connection.connect();
  connection.disconnect(); // 刹车收掉退避重连的定时器
  await assert.rejects(connection.request("lg:approvals.list"), /not connected/);
});

test("a daemon Command is still dispatched, not mistaken for a reply", async () => {
  const p = port();
  p.reply({ id: 7, method: "lg:nonsense.do", params: {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const out = p.sent.at(-1) as Any;
  assert.equal(out.type, "error");
  assert.equal(out.id, 7);
  assert.equal(out.error, "unsupported operation");
});
