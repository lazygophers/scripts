import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setEventSink } from "../src/events.ts";
import { listenPrinting, printingRespond } from "../src/handlers/printing.ts";
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

afterEach(() => {
  clearChrome();
  setEventSink(null);
});

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
    const ids = stub.events.map((e) => (e.params as Any).request);
    assert.notEqual(ids[0], ids[1]);
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
