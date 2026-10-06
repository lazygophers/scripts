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
  static readonly OPEN = 1;
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

// ---------------------------------------------------------------- 更多分支

test("构造抛异常时进重连排队，状态报出原因", async () => {
  boot();
  FakeSocket.throwNext = true;
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  assert.equal(conn.status().state, "disconnected");
  assert.match(conn.status().reason ?? "", /bridge|unreachable|连/);
  assert.equal(conn.status().attempt >= 1, true, "排了一次重连");
  conn.disconnect(); // 清掉重连定时器，别吊住测试进程
});

test("已有 socket 时 connect() 是幂等空操作", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const before = sockets.length;
  conn.connect();
  await settle();
  assert.equal(sockets.length, before, "不能重复建 socket");
});

test("onopen 的 hello：socket 被换掉后不再补发", async () => {
  boot();
  const sent: string[] = [];
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const stale = sockets[0]!;
  stale.send = (data: string) => sent.push(data);
  // 同步触发 onopen 后立刻 disconnect：instanceId 的微任务落地时 socket 已被换掉。
  // 顺序不能反——onopen 会启动 ping 定时器，disconnect 先跑就没人收它了。
  stale.onopen?.({});
  conn.disconnect();
  await settle();
  assert.equal(sent.filter((s) => s.includes("hello")).length, 0);
});

test("onmessage 丢掉非字符串和非 JSON 的帧", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1; // OPEN，让 send 能走
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  s.onmessage?.({ data: {} as unknown as string });
  s.onmessage?.({ data: "{nope" });
  await settle();
  assert.equal(out.length, 0, "坏帧不该触发任何回包");
});

test("指令走 dispatch：成功回 success，未知方法回 error，日志倒序", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  s.onmessage?.({ data: JSON.stringify({ id: 1, method: "lg:audit.read", params: { limit: 1 } }) });
  await settle();
  s.onmessage?.({ data: JSON.stringify({ id: 2, method: "no.such.method", params: {} }) });
  await settle();
  assert.match(out[0] ?? "", /"type":"success"/);
  assert.match(out[1] ?? "", /"type":"error"/);
  assert.match(out[1] ?? "", /unsupported operation|no handler/i);
  const recent = conn.recent();
  assert.equal(recent[0].method, "no.such.method", "recent() 新的在前");
  assert.equal(recent[0].ok, false);
});

test("回包路由：request 的 success/error 各自 settle", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  const ok = conn.request("lg:approvals.list");
  await settle();
  const sentId = (JSON.parse(out[0]) as Any).id;
  s.onmessage?.({ data: JSON.stringify({ id: sentId, type: "success", result: { yes: 1 } }) });
  assert.deepEqual(await ok, { yes: 1 });

  const bad = conn.request("lg:approvals.list");
  await settle();
  const sentId2 = (JSON.parse(out[1]) as Any).id;
  s.onmessage?.({ data: JSON.stringify({ id: sentId2, type: "error", error: "lg:user rejected", message: "no" }) });
  await assert.rejects(bad, /no/);
});

test("断线把还挂着的 request 全部按 not connected 拒掉", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  s.send = () => {};
  const pending = conn.request("lg:approvals.list");
  // 结算走 onclose → scheduleReconnect，不是 disconnect()（它只刹车不回包）
  s.onclose?.();
  await assert.rejects(
    pending,
    (err: Error & { code?: string }) => err.code === "lg:browser not connected",
  );
  conn.disconnect();
});

test("status(): OPEN 才算 connected，日志满 20 条滚掉最老的", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  s.send = () => {};
  s.onmessage?.({ data: JSON.stringify({ id: 1, method: "lg:audit.read", params: {} }) });
  await settle();
  assert.equal(conn.status().state, "connected");
  assert.ok(conn.status().lastMessageAt > 0, "收到消息要记账");

  for (let i = 0; i < 25; i += 1) {
    s.onmessage?.({ data: JSON.stringify({ id: 100 + i, method: "lg:audit.read", params: {} }) });
    await settle();
  }
  assert.equal(conn.recent().length, 20, "LOG_SIZE=20");
});

test("hello-ack 和未知形状的消息都不当成指令", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  s.onmessage?.({ data: JSON.stringify({ type: "hello-ack" }) });
  s.onmessage?.({ data: JSON.stringify({ random: true }) });
  await settle();
  assert.equal(out.length, 0);
});



// ------------------------------------------------- instanceId / browserName / hello

function setBrands(brands: { brand: string }[] | undefined): void {
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { userAgentData: brands === undefined ? undefined : { brands } },
  });
}

test("onopen 发 hello：浏览器名和持久化的 instanceId 都带上", async () => {
  boot();
  setBrands([{ brand: "Google Chrome" }]);
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  s.onopen?.({});
  await settle();
  await new Promise((r) => setTimeout(r, 1));
  const hello = JSON.parse(out.find((x) => x.includes("hello")) ?? "{}") as Any;
  assert.equal(hello.browser, "chrome");
  assert.equal(typeof hello.instanceId, "string");

  // 第二次 onopen：instanceId 命中缓存分支；brands 换成认不出的 → chromium
  setBrands([{ brand: "Not A Chromium" }]);
  s.onopen?.({});
  await new Promise((r) => setTimeout(r, 1));
  const hello2 = JSON.parse(out.filter((x) => x.includes("hello")).at(-1) ?? "{}") as Any;
  assert.equal(hello2.browser, "chromium");
  assert.equal(hello2.instanceId, hello.instanceId, "缓存的 instanceId 复用");
  setBrands(undefined);
  conn.disconnect();
});

test("browserName 认得 Edge 和 brave/opera/vivaldi/arc", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  const s = sockets[0]!;
  s.readyState = 1;
  const out: string[] = [];
  s.send = (data: string) => out.push(data);
  for (const brand of ["Microsoft Edge", "Brave", "Opera", "Vivaldi", "Arc"]) {
    setBrands([{ brand }]);
    s.onopen?.({});
    await new Promise((r) => setTimeout(r, 1));
  }
  const names = out
    .filter((x) => x.includes("hello"))
    .map((x) => (JSON.parse(x) as Any).browser);
  assert.deepEqual(names, ["edge", "brave", "opera", "vivaldi", "arc"]);
  setBrands(undefined);
  conn.disconnect();
});

test("storage 读不到 instanceId 时现场生成也能连", async () => {
  boot();
  (globalThis.chrome as Any).storage = {
    local: {
      get: async () => {
        throw new Error("no storage");
      },
      set: async () => {},
    },
  };
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  assert.equal(sockets.length, 1, "loadStopped 的 catch 退化为没刹过车，照常连");
  sockets[0]!.readyState = 1;
  const out: string[] = [];
  sockets[0]!.send = (data: string) => out.push(data);
  sockets[0]!.onopen?.({});
  await new Promise((r) => setTimeout(r, 1));
  const hello = JSON.parse(out.find((x) => x.includes("hello")) ?? "{}") as Any;
  assert.equal(typeof hello.instanceId, "string");
  // persistStopped 的 catch（set 抛）也不影响刹车本身
  (globalThis.chrome as Any).storage.local.set = async () => {
    throw new Error("quota");
  };
  conn.disconnect();
  assert.equal(conn.status().stopped, true);
});

test("旧 socket 的 onclose 在换了新 socket 后是空操作", async () => {
  boot();
  const states: string[] = [];
  const conn = new NativeConnection(() => {}, (st: string) => states.push(st));
  conn.connect();
  await settle();
  const stale = sockets[0]!;
  stale.readyState = 1;
  stale.onclose?.(); // 掉线 → 排重连
  await settle();
  // 立刻把「新 socket」造出来（重连定时器还没到，直接再 connect 不行——旧 socket
  // 已置 null，connect 会建新的）
  conn.connect();
  await settle();
  const before = states.length;
  stale.onclose?.(); // 旧 socket 又冒泡一次：守卫挡住，不再排重连
  await settle();
  assert.equal(states.length, before, "过期 onclose 不该再触发状态变化");
  conn.disconnect();
});

test("send() 在没连上时静默丢弃，scheduleReconnect 尊重已排的重连", async () => {
  boot();
  const conn = new NativeConnection(() => {}, () => {});
  conn.send({ type: "event", method: "lg:keepalive.ping", params: {} }); // readyState 0：丢
  FakeSocket.throwNext = true;
  conn.connect(); // 抛 → 排重连
  await settle();
  const attempt = conn.status().attempt;
  conn.send({ type: "event", method: "lg:keepalive.ping", params: {} });
  assert.equal(conn.status().attempt, attempt, "重连已排队，不再叠");
  conn.disconnect();
});

test("describe：非 Error 的异常对象也给出可读文案", async () => {
  boot();
  class WeirdThrow {
    static next: unknown;
  }
  void WeirdThrow;
  FakeSocket.throwNext = false;
  // 构造时抛一个带 message 的普通对象（Web 平台某些 API 的实际行为）
  const origCtor = (globalThis as Any).WebSocket;
  (globalThis as Any).WebSocket = class {
    constructor() {
      throw { message: "port blew up" };
    }
  };
  const conn = new NativeConnection(() => {}, () => {});
  conn.connect();
  await settle();
  (globalThis as Any).WebSocket = origCtor;
  assert.match(conn.status().reason ?? "", /port blew up/);
  conn.disconnect();
});
