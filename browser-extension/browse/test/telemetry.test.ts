import assert from "node:assert/strict";
import test, { afterEach, mock } from "node:test";
import {
  FLUSH_DEBOUNCE_MS,
  QUEUE_KEY,
  RING_MAX,
  flush,
  log,
  resetForTests,
  setTelemetrySink,
} from "../src/telemetry.ts";
import { clearChrome, installChrome, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

function queue(store: Any): Any[] {
  return (store.data[QUEUE_KEY] as Any[]) ?? [];
}

/** 把 storage 串行链排干：setImmediate 前微任务必清空。 */
async function settle(): Promise<void> {
  await new Promise((resolve) => setImmediate(resolve));
}

afterEach(() => {
  resetForTests();
  setTelemetrySink(null);
  clearChrome();
});

test("info only enters the queue when allowlisted; warning and error always do", async () => {
  const store = storageMock();
  log("some.noise", "info", {});
  log("ws.connected", "info", { attempt: 0 });
  log("sw-start", "info");
  log("ws.reconnect.scheduled", "warning", { reason: "r" });
  log("handler.crashed", "error", {});
  await settle();
  assert.deepEqual(queue(store).map((e) => e.event), [
    "ws.connected",
    "sw-start",
    "ws.reconnect.scheduled",
    "handler.crashed",
  ]);
});

test("entries pass through clean() before enqueueing", async () => {
  const store = storageMock();
  log("ws.reconnect.scheduled", "warning", {
    reason: "bridge died holding sk-ant-api03-abcdefghijklmnopqrst",
  });
  await settle();
  const entry = queue(store)[0] as Any;
  assert.equal((entry.fields as Any).reason, "bridge died holding [REDACTED]");
  assert.equal(typeof entry.t, "number");
});

test("the ring keeps the newest 100 entries and drops the oldest", async () => {
  const store = storageMock();
  for (let i = 0; i < RING_MAX + 5; i++) {
    log("ws.reconnect.scheduled", "warning", { i });
  }
  await settle();
  const q = queue(store);
  assert.equal(q.length, RING_MAX);
  assert.equal((q[0]!.fields as Any).i, 5);
  assert.equal((q.at(-1)!.fields as Any).i, RING_MAX + 4);
});

test("flush clears entries on ack and keeps them on reject", async () => {
  const store = storageMock();
  log("ws.connected", "info", { attempt: 3 });
  await settle();
  let fail = true;
  const batches: Any[][] = [];
  setTelemetrySink(async (entries) => {
    batches.push(entries);
    if (fail) {
      throw new Error("bridge gone");
    }
    return { accepted: entries.length, dropped: 0 };
  });
  flush();
  await settle();
  assert.equal(queue(store).length, 1, "reject 保留整批");
  fail = false;
  flush();
  await settle();
  assert.deepEqual(queue(store), [], "ack 后清空");
  assert.deepEqual(batches[0]!.map((e) => e.event), ["ws.connected"]);
});

test("flush without a sink leaves the queue alone", async () => {
  const store = storageMock();
  log("sw-start", "info");
  await settle();
  flush();
  await settle();
  assert.equal(queue(store).length, 1);
});

test("a broken storage never crashes the caller and never enqueues", async () => {
  installChrome({
    storage: {
      local: {
        get: async () => {
          throw new Error("storage gone");
        },
        set: async () => {
          throw new Error("storage gone");
        },
      },
    },
  });
  assert.doesNotThrow(() => log("ws.connected", "info", {}));
  await settle();
  flush();
  await settle();
});

test("a failing storage.set drops the entry instead of retrying", async () => {
  const store = storageMock();
  store.failNext = 1;
  log("sw-start", "info");
  await settle();
  assert.equal(store.data[QUEUE_KEY], undefined);
});

test("flush after an enqueue is debounced and batches the backlog", async () => {
  const store = storageMock();
  let calls = 0;
  setTelemetrySink(async (entries) => {
    calls += 1;
    return { accepted: entries.length, dropped: 0 };
  });
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    log("ws.connected", "info", {});
    log("sw-start", "info");
    mock.timers.tick(FLUSH_DEBOUNCE_MS - 1);
    assert.equal(calls, 0, "防抖期内不发");
    mock.timers.tick(1);
    await settle();
    assert.equal(calls, 1, "到点发一批");
    assert.deepEqual(queue(store), [], "发完整批清空");
  } finally {
    mock.timers.reset();
  }
});
