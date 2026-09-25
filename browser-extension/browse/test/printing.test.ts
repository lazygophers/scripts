import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it, mock } from "node:test";

import { setEventSink } from "../src/events.ts";
import { PRINT_TIMEOUT_MS, listenPrinting, printingRespond } from "../src/handlers/printing.ts";
import type { Event } from "../src/protocol.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

/**
 * printerProvider 的桩：`onPrintRequested.addListener` 把监听器存下来，用例自己
 * 触发一次打印请求，拿到 requestId 后再走 printingRespond。
 */
function printerProviderWith() {
  type Listener = (job: Any, callback: (status: string) => void) => void;
  const listeners: Listener[] = [];
  const events: Event[] = [];
  installChrome({
    printerProvider: {
      onPrintRequested: { addListener: (fn: Listener) => void listeners.push(fn) },
    },
  });
  setEventSink((event) => void events.push(event));
  return {
    events,
    /** 模拟 Chrome 打印对话框把活派过来，返回收集打印结果的数组。 */
    fire(printerId = "printer-1"): string[] {
      const answers: string[] = [];
      listeners[0]({ printerId, ticket: { copies: 2 } }, (status) => void answers.push(status));
      return answers;
    },
  };
}

// 每条 pending 请求都挂着一个 5 分钟的超时定时器。全程用假定时器：既让超时可测，
// 也不让没被回答的请求留下真定时器把 node --test 吊住五分钟。Date 一起冻住，
// 撞号那条用例才能稳定复现「同一毫秒」。
beforeEach(() => {
  mock.timers.enable({ apis: ["Date", "setTimeout"] });
});

afterEach(() => {
  clearChrome();
  setEventSink(null);
  mock.timers.reset();
});

/** 取一条事件里的 request id。 */
function idOf(events: Event[], index: number): string {
  return (events[index].params as Any).request as string;
}

describe("listenPrinting", () => {
  it("does nothing when the browser has no printerProvider", () => {
    installChrome({});
    // 可选链兜住：不该抛，只是这台机器上没有这条通道
    assert.doesNotThrow(() => listenPrinting());
  });

  it("forwards a print request as an lg:printing.request event", () => {
    const stub = printerProviderWith();
    listenPrinting();
    stub.fire("HP-1");
    assert.equal(stub.events.length, 1);
    assert.equal(stub.events[0].method, "lg:printing.request");
    const params = stub.events[0].params as Any;
    assert.equal(params.printer, "HP-1");
    assert.deepEqual(params.ticket, { copies: 2 });
    assert.equal(typeof params.request, "string");
  });

  it("gives each pending request its own id", () => {
    const stub = printerProviderWith();
    listenPrinting();
    stub.fire();
    stub.fire();
    assert.notEqual(idOf(stub.events, 0), idOf(stub.events, 1));
  });

  it("keeps ids unique after one was answered in the same millisecond", async () => {
    // 时钟冻住，复现旧写法（序号 = map.size + 1，会随 respond 回落）的撞号：
    // A 进 → B 进 → 答 A → C 进，C 会拿到和 B 一样的 id 并把 B 顶掉
    const stub = printerProviderWith();
    listenPrinting();
    stub.fire("A");
    const bAnswers = stub.fire("B");
    await printingRespond({ request: idOf(stub.events, 0), status: "OK" });
    stub.fire("C");
    const ids = [0, 1, 2].map((i) => idOf(stub.events, i));
    assert.equal(new Set(ids).size, 3, "三条请求必须拿到三个不同的 id");
    // B 还在等着，顶掉它就等于 Chrome 那边的对话框永远挂着
    await printingRespond({ request: ids[1], status: "OK" });
    assert.deepEqual(bAnswers, ["OK"]);
  });

  it("fails a request nobody answered instead of hanging Chrome's dialog", () => {
    const stub = printerProviderWith();
    listenPrinting();
    const answers = stub.fire();
    assert.deepEqual(answers, [], "超时之前不回调");
    mock.timers.tick(PRINT_TIMEOUT_MS);
    assert.deepEqual(answers, ["FAILED"]);
  });

  it("drops a timed-out request from the pending map", async () => {
    const stub = printerProviderWith();
    listenPrinting();
    stub.fire();
    const request = idOf(stub.events, 0);
    mock.timers.tick(PRINT_TIMEOUT_MS);
    await rejectsWith(() => printingRespond({ request, status: "OK" }), "invalid argument");
  });

  it("an answered request never fires its timeout", async () => {
    const stub = printerProviderWith();
    listenPrinting();
    const answers = stub.fire();
    await printingRespond({ request: idOf(stub.events, 0), status: "OK" });
    mock.timers.tick(PRINT_TIMEOUT_MS);
    assert.deepEqual(answers, ["OK"], "回调只能调一次，Chrome 第二次会抛");
  });
});

describe("printingRespond", () => {
  it("refuses when the browser has no printerProvider", async () => {
    installChrome({});
    await rejectsWith(
      () => printingRespond({ request: "print-1", status: "OK" }),
      "unsupported operation",
    );
  });

  it("needs a request id", async () => {
    printerProviderWith();
    await rejectsWith(() => printingRespond({ status: "OK" }), "invalid argument");
  });

  it("needs a status", async () => {
    printerProviderWith();
    await rejectsWith(() => printingRespond({ request: "print-1" }), "invalid argument");
  });

  it("rejects a status outside the four Chrome accepts", async () => {
    printerProviderWith();
    await rejectsWith(
      () => printingRespond({ request: "print-1", status: "DONE" }),
      "invalid argument",
    );
  });

  it("refuses an id that has no pending request", async () => {
    printerProviderWith();
    await rejectsWith(
      () => printingRespond({ request: "print-never", status: "OK" }),
      "invalid argument",
    );
  });

  it("hands the status back to Chrome's callback", async () => {
    const stub = printerProviderWith();
    listenPrinting();
    const answers = stub.fire();
    const request = (stub.events[0].params as Any).request as string;
    assert.deepEqual(await printingRespond({ request, status: "INVALID_TICKET" }), {
      responded: request,
    });
    assert.deepEqual(answers, ["INVALID_TICKET"]);
  });

  it("answers each request only once", async () => {
    const stub = printerProviderWith();
    listenPrinting();
    stub.fire();
    const request = (stub.events[0].params as Any).request as string;
    await printingRespond({ request, status: "OK" });
    // Chrome 的 callback 调第二次会抛，所以第二次必须在这里就被拦住
    await rejectsWith(() => printingRespond({ request, status: "OK" }), "invalid argument");
  });
});
