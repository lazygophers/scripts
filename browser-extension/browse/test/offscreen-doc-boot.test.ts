/**
 * offscreen 文档的失败路径：没在录就停、getUserMedia 起不来（Error 和非 Error）。
 * offscreen-doc.test.ts 共用一个模块实例走完整录制，这些「坏起点」需要独立文件
 * 的一次独立导入（同进程里同路径的重复 import 只有最后一个实例进覆盖率）。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";

type Any = Record<string, unknown>;
const g = globalThis as Any;

afterEach(() => {
  delete g.chrome;
  delete g.MediaRecorder;
  delete g.navigator;
});

test("没在录就停、起录失败，都 fail closed 且不回应之外的状态", async () => {
  class FakeStream {}
  class FakeRecorder {
    ondataavailable: ((ev: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    constructor(_stream: FakeStream, _opts: { mimeType: string }) {}
    start(): void {}
    stop(): void {
      this.onstop?.();
    }
  }
  let getUserMediaError: unknown = new Error("no such source") as unknown;
  let failOdd = false;

  let listener: ((m: unknown, s: unknown, r: (x: unknown) => void) => unknown) | undefined;
  g.chrome = {
    runtime: {
      onMessage: {
        addListener: (fn: never) => {
          listener = fn as unknown as typeof listener;
        },
      },
    },
  };
  g.MediaRecorder = FakeRecorder;
  Object.defineProperty(g, "navigator", {
    configurable: true,
    value: {
      mediaDevices: {
        getUserMedia: async () => {
          if (failOdd) throw 42;
          if (getUserMediaError) throw getUserMediaError;
          return new FakeStream();
        },
      },
    },
  });

  await import("../src/offscreen-doc.ts");
  assert.ok(listener);
  const send = (message: unknown): unknown[] => {
    const out: unknown[] = [];
    listener!(message, {}, (x: unknown) => void out.push(x));
    return out;
  };
  const sendAsync = async (message: unknown): Promise<unknown[]> => {
    const out = send(message);
    await new Promise((resolve) => setTimeout(resolve, 0)); // 起录失败是异步应答
    return out;
  };

  assert.deepEqual(send({ type: "lg:record-stop" }), [{ ok: false, error: "not recording" }]);

  assert.deepEqual(await sendAsync({ type: "lg:record-start", id: "r4", kind: "tab", streamId: "s4" }), [
    { ok: false, error: "no such source" },
  ]);

  getUserMediaError = null;
  failOdd = true;
  assert.deepEqual(await sendAsync({ type: "lg:record-start", id: "r5", kind: "tab", streamId: "s5" }), [
    { ok: false, error: "42" },
  ]);
});
