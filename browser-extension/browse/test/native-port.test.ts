import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { NativeConnection } from "../src/native-port.ts";
import { clearChrome, installChrome, storageMock } from "./mock.ts";

type Any = Record<string,unknown>;

/**
 * 停止（stopped）状态的单一所有权：用户刹车后跨 service worker 重启保持，
 * alarm 兜底重连尊重它，浏览器完整启动（resume）才解除。
 * 2026-09-21 之前 background.ts 还有一个并行的 braked，SW 一被杀两者同时
 * 失忆；这两个测试就是钉住「不再有第二个状态源」。
 */

const sockets: FakeSocket[] = [];
let previousWebSocket: unknown;

class FakeSocket {
  static throwNext = false;
  readyState = 0;
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

  send(): void {}

  close(): void {
    this.readyState = 0;
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
