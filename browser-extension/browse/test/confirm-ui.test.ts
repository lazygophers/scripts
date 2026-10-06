/**
 * 确认弹窗的编排逻辑：窗口开了等答案、关窗即拒绝、答案迟到找不到人就丢弃、
 * 五秒内同题免弹。DOM 页面本身（confirm.html）不在测试范围里。
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { answerConfirm, askUser, windowClosed } from "../src/confirm-ui.ts";
import { clearChrome, installChrome } from "./mock.ts";

type Any = Record<string, unknown>;

const REQ = { action: "readCookies" as const, method: "storage.getCookies", url: "https://a.test/" };

function world(opts: {
  createdId?: number | undefined;
  createThrows?: boolean;
  removeThrows?: boolean;
} = {}): { created: Any[]; removed: number[] } {
  const calls = { created: [] as Any[], removed: [] as number[] };
  /** 弹窗 URL 里的 token：seq 是模块状态，不能在测试里硬编码。 */
  calls.lastToken = () => {
    const url = String(calls.created.at(-1)?.url ?? "");
    return new URL(url).searchParams.get("token") ?? "";
  };
  installChrome({
    runtime: { getURL: (p: string) => `chrome-extension://id/${p}` },
    windows: {
      create: async (o: Any) => {
        if (opts.createThrows) {
          throw new Error("no windows");
        }
        calls.created.push(o);
        return opts.createdId === undefined ? undefined : { id: opts.createdId };
      },
      remove: async (id: number) => {
        if (opts.removeThrows) {
          throw new Error("gone");
        }
        calls.removed.push(id);
      },
    },
  });
  return calls;
}

afterEach(clearChrome);

test("the answer comes back through the token and closes the window", async () => {
  const calls = world({ createdId: 42 });
  const pending = askUser(REQ);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(await answerConfirm("1", true), true);
  assert.equal(await pending, true);
  assert.deepEqual(calls.removed, [42]);
});

test("a rejected answer resolves false and still closes", async () => {
  const calls = world({ createdId: 42 });
  const req = { ...REQ, action: "writeCookies" as const };
  const pending = askUser(req);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(await answerConfirm(calls.lastToken(), "anything but true"), true, "有人等：送达");
  assert.equal(await pending, false, "不是明确的 yes 一律算拒绝");
  assert.deepEqual(calls.removed, [42]);
});

test("closing the dialog is a refusal; a late answer finds nobody", async () => {
  const calls = world({ createdId: 42 });
  const pending = askUser({ ...REQ, action: "readHistory" as const });
  await new Promise((r) => setTimeout(r, 0));
  windowClosed(42);
  assert.equal(await pending, false);
  assert.equal(answerConfirm("1", true), false, "迟到的一票不算");
  assert.equal(answerConfirm({ bogus: 1 }, true), false, "非字符串 token 不查表");
});

test("a window that never opened resolves false; an answered race closes it", async () => {
  world({ createdId: undefined });
  assert.equal(await askUser({ ...REQ, action: "readPage" as const }), false);

  const calls = world({ createdId: 7 });
  const early = askUser({ ...REQ, action: "evalMainWorld" as const });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(answerConfirm(calls.lastToken(), true), true);
  assert.equal(await early, true);

  // 窗口还没开就有人答了：开出来的空窗口要被关掉
  const late = askUser({ ...REQ, action: "adoptTab" as const });
  assert.equal(answerConfirm(calls.lastToken(), true), true);
  assert.equal(await late, true);
  await new Promise((r) => setTimeout(r, 0));
});

test("create throwing resolves false; remove throwing is swallowed", async () => {
  world({ createThrows: true });
  assert.equal(await askUser({ ...REQ, action: "readClipboard" as const }), false);

  const calls = world({ createdId: 42, removeThrows: true });
  const pending = askUser({ ...REQ, action: "download" as const });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(answerConfirm(calls.lastToken(), true), true);
  assert.equal(await pending, true);
});

test("the same question within five seconds reuses the answer", async () => {
  const calls = world({ createdId: 42 });
  const first = askUser({ ...REQ, action: "captureMedia" as const });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(answerConfirm(calls.lastToken(), true), true);
  assert.equal(await first, true);
  // 第二次不弹窗直接复用（window.create 若被调会往同一个 calls 里加第二条）
  assert.equal(await askUser(REQ), true);
});
