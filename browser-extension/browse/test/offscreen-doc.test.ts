/**
 * offscreen 文档：SW 发消息进来，这边动 MediaRecorder 和剪贴板。
 * offscreen-doc.ts 没有导出：import 即注册 onMessage。量的是：三类指令的应答
 * 形态（含异步）、录制全流程（起录、重复起录被拒、收块、停录回 base64、收轨道）、
 * 剪贴板读写的成功与失败、不认识的消息不回应。
 * 一个文件只 import 一次（同 URL 再 import 是缓存命中）；getUserMedia 起不来的
 * 分支在 offscreen-doc-boot.test.ts。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";

type Any = Record<string, unknown>;
const g = globalThis as Any;

class FakeStream {
  stopped = 0;
  getTracks(): { stop: () => void }[] {
    return [{ stop: () => (this.stopped += 1) }];
  }
}

class FakeRecorder {
  ondataavailable: ((ev: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  started: number | null = null;
  mimeType = "";
  stream: FakeStream;

  constructor(stream: FakeStream, opts: { mimeType: string }) {
    this.stream = stream;
    this.mimeType = opts.mimeType;
    instances.push(this);
  }
  start(timeslice: number): void {
    this.started = timeslice;
  }
  stop(): void {
    this.onstop?.();
  }
}

const instances: FakeRecorder[] = [];
let listener: ((m: unknown, s: unknown, r: (x: unknown) => void) => unknown) | undefined;
let clipboardError: unknown = null;
let clipboardText = "";
const written: string[] = [];

async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function send(message: unknown): { respond: unknown[]; result: unknown } {
  const respond: unknown[] = [];
  const result = listener!(message, {}, (x: unknown) => void respond.push(x));
  return { respond, result };
}

afterEach(() => {
  delete g.chrome;
  delete g.MediaRecorder;
  delete g.navigator;
});

test("录制的全流程：起录、重复起录被拒、收块、停录回 base64 并收轨道", async () => {
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
  const stream = new FakeStream();
  Object.defineProperty(g, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async () => stream } },
  });

  await import("../src/offscreen-doc.ts");
  assert.ok(listener, "模块必须注册 onMessage 监听");

  // 不认识的消息、没有 type 字段的消息、null 都不回应
  assert.deepEqual(send({ type: "lg:zzz" }).respond, []);
  assert.deepEqual(send({}).respond, []);
  assert.deepEqual(send(null).respond, []);
  assert.equal(send(null).result, false);

  const start = send({ type: "lg:record-start", id: "r1", kind: "tab", streamId: "s1" });
  assert.equal(start.result, true, "起录是异步应答");
  await settle();
  assert.deepEqual(start.respond, [{ ok: true }]);
  assert.equal(instances.length, 1);
  assert.equal(instances[0]!.started, 1000);
  assert.equal(instances[0]!.mimeType, "video/webm");

  const again = send({ type: "lg:record-start", id: "r2", kind: "desktop", streamId: "s2" });
  await settle();
  assert.deepEqual(again.respond, [
    { ok: false, error: "offscreen document is already recording" },
  ]);
  assert.equal(instances.length, 1, "重复起录不再建 recorder");

  instances[0]!.ondataavailable?.({ data: new Blob(["aa"]) });
  instances[0]!.ondataavailable?.({ data: new Blob([]) }); // 空块不进池子

  const stop = send({ type: "lg:record-stop" });
  await settle();
  const reply = stop.respond[0] as Any;
  assert.equal(reply.ok, true);
  assert.equal(reply.id, "r1");
  assert.equal(reply.kind, "tab");
  assert.equal(typeof reply.seconds, "number");
  assert.equal(reply.base64, Buffer.from("aa").toString("base64"));
  assert.equal(reply.bytes, 2);
  assert.equal(stream.stopped, 1, "停录要收掉轨道");

  // desktop 源同样能起录（kind 只影响约束里的 chromeMediaSource）
  const desktop = send({ type: "lg:record-start", id: "r3", kind: "desktop", streamId: "s3" });
  await settle();
  assert.deepEqual(desktop.respond, [{ ok: true }]);
});

test("剪贴板读写与失败降级", async () => {
  assert.ok(listener, "同一个 offscreen 文档实例");
  clipboardText = "CLIP";
  written.length = 0;
  clipboardError = null;
  Object.defineProperty(g, "navigator", {
    configurable: true,
    value: {
      clipboard: {
        readText: async () => {
          if (clipboardError) throw clipboardError;
          return clipboardText;
        },
        writeText: async (text: string) => {
          if (clipboardError) throw clipboardError;
          written.push(text);
        },
      },
    },
  });

  const read = send({ type: "lg:clipboard", op: "read" });
  assert.equal(read.result, true, "剪贴板是异步应答");
  await settle();
  assert.deepEqual(read.respond, [{ ok: true, text: "CLIP" }]);

  const write = send({ type: "lg:clipboard", op: "write", text: "hello" });
  await settle();
  assert.deepEqual(write.respond, [{ ok: true }]);
  assert.deepEqual(written, ["hello"]);

  const blank = send({ type: "lg:clipboard", op: "write" });
  await settle();
  assert.deepEqual(blank.respond, [{ ok: true }]);
  assert.deepEqual(written, ["hello", ""]);

  clipboardError = new Error("denied");
  const failed = send({ type: "lg:clipboard", op: "read" });
  await settle();
  assert.deepEqual(failed.respond, [{ ok: false, error: "denied" }]);

  clipboardError = "not an error";
  const odd = send({ type: "lg:clipboard", op: "write", text: "x" });
  await settle();
  assert.deepEqual(odd.respond, [{ ok: false, error: "not an error" }]);
});
