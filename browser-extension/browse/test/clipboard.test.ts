import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { clipboardRead, clipboardWrite } from "../src/handlers/clipboard.ts";
import { setConfirmHook } from "../src/handlers/confirm.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

const OFFSCREEN_REASONS = { CLIPBOARD: "CLIPBOARD", DISPLAY_MEDIA: "DISPLAY_MEDIA", USER_MEDIA: "USER_MEDIA" };

/**
 * service worker 没有 DOM，剪贴板的活转给 offscreen 文档，所以桩要同时提供
 * runtime（getContexts / sendMessage）和 offscreen 两个 namespace。
 * `withOffscreen: false` 用来测 ensureOffscreen 的拒绝路径。
 */
function chromeWith(
  reply: unknown,
  { withOffscreen = true, contexts = [] as Any[], config = DEFAULTS as Any } = {},
) {
  const messages: Any[] = [];
  const extra: Any = {
    runtime: {
      getContexts: async () => contexts,
      getURL: (path: string) => `chrome-extension://id/${path}`,
      sendMessage: async (message: Any) => {
        messages.push(message);
        return reply;
      },
    },
  };
  if (withOffscreen) {
    extra.offscreen = { Reason: OFFSCREEN_REASONS, createDocument: async () => undefined };
  }
  installChrome(extra);
  storageMock({ [CONFIG_KEY]: config });
  setConfirmHook(async () => true);
  return messages;
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("clipboardRead", () => {
  it("refuses when the browser has no offscreen API", async () => {
    chromeWith({ ok: true, text: "x" }, { withOffscreen: false });
    await rejectsWith(() => clipboardRead(), "unsupported operation");
  });

  it("returns the text the offscreen document read", async () => {
    const messages = chromeWith({ ok: true, text: "剪贴板内容" });
    assert.deepEqual(await clipboardRead(), { text: "剪贴板内容" });
    assert.deepEqual(messages[0], { type: "lg:clipboard", op: "read" });
  });

  it("reports an empty clipboard as an empty string, not undefined", async () => {
    chromeWith({ ok: true });
    assert.deepEqual(await clipboardRead(), { text: "" });
  });

  it("surfaces the offscreen error message", async () => {
    chromeWith({ ok: false, error: "拒绝访问剪贴板" });
    await assert.rejects(() => clipboardRead(), /拒绝访问剪贴板/);
  });

  it("has a fallback message when the offscreen document never answers", async () => {
    chromeWith(undefined);
    await assert.rejects(() => clipboardRead(), /clipboard read failed/);
  });

  it("refuses when the user denies reading the clipboard", async () => {
    // 剪贴板里常是密码管理器刚复制的密码或 2FA 码，policy.ts 的 RISKY_METHODS
    // 把 lg:clipboard.read 标成 readClipboard 高危动作，拦截点就在这里
    const messages = chromeWith({ ok: true, text: "秘密" }, {
      config: { ...DEFAULTS, confirm_mode: "always" },
    });
    setConfirmHook(async () => false);
    await rejectsWith(() => clipboardRead(), "lg:user rejected");
    assert.equal(messages.length, 0, "拒绝要发生在真正读之前");
  });

  it("asks with the readClipboard action and no target url", async () => {
    const seen: Any[] = [];
    chromeWith({ ok: true, text: "x" }, { config: { ...DEFAULTS, confirm_mode: "always" } });
    setConfirmHook(async (request) => {
      seen.push(request as unknown as Any);
      return true;
    });
    await clipboardRead();
    assert.equal(seen[0].action, "readClipboard");
    assert.equal(seen[0].method, "lg:clipboard.read");
    assert.equal(seen[0].url, null);
  });
});

describe("clipboardWrite", () => {
  it("needs a non-empty text", async () => {
    chromeWith({ ok: true });
    await rejectsWith(() => clipboardWrite({}), "invalid argument");
    await rejectsWith(() => clipboardWrite({ text: "" }), "invalid argument");
  });

  it("rejects a non-string text", async () => {
    chromeWith({ ok: true });
    await rejectsWith(() => clipboardWrite({ text: 1 }), "invalid argument");
  });

  it("forwards the text and reports how much was written", async () => {
    const messages = chromeWith({ ok: true });
    assert.deepEqual(await clipboardWrite({ text: "四个字符" }), { wrote: 4 });
    assert.deepEqual(messages[0], { type: "lg:clipboard", op: "write", text: "四个字符" });
  });

  it("surfaces the offscreen error message", async () => {
    chromeWith({ ok: false, error: "写入失败" });
    await assert.rejects(() => clipboardWrite({ text: "x" }), /写入失败/);
  });

  it("validates before creating an offscreen document", async () => {
    // 参数错就不该留下一个空转的 offscreen 文档
    let created = false;
    installChrome({
      runtime: { getContexts: async () => [], getURL: (p: string) => p, sendMessage: async () => ({ ok: true }) },
      offscreen: {
        Reason: OFFSCREEN_REASONS,
        createDocument: async () => {
          created = true;
        },
      },
    });
    storageMock({ [CONFIG_KEY]: DEFAULTS });
    await rejectsWith(() => clipboardWrite({}), "invalid argument");
    assert.equal(created, false);
  });
});
