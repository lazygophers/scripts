import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setConfirmHook } from "../src/handlers/confirm.ts";
import {
  captureRecordDesktop,
  captureRecordStop,
  captureRecordTab,
  desktopSourcePicked,
  ensureOffscreen,
  offscreenDocuments,
  pageCaptureSaveMhtml,
} from "../src/handlers/capture.ts";
import { dropContextCache } from "../src/handlers/context.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

const OFFSCREEN_REASONS = { CLIPBOARD: "CLIPBOARD", DISPLAY_MEDIA: "DISPLAY_MEDIA", USER_MEDIA: "USER_MEDIA" };

/** 一份够用的 chrome 桩：只装用例点名的 namespace，其余缺席正好触发 requireApi。 */
function chromeWith(extra: Any, contexts: Any[] = [], config: Any = DEFAULTS): Any {
  const installed = installChrome({
    runtime: {
      getContexts: async () => contexts,
      getURL: (path: string) => `chrome-extension://id/${path}`,
      sendMessage: async () => ({ ok: true }),
    },
    ...extra,
  });
  storageMock({ [CONFIG_KEY]: config });
  // 目标标签页的解析结果有模块级缓存，跨用例会把上一个用例的 tabId 带过来
  dropContextCache({});
  // 策略裁决单独有 policy.test.ts 覆盖，这里只关心 capture 自己的逻辑
  setConfirmHook(async () => true);
  return installed;
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("ensureOffscreen", () => {
  it("refuses when the browser has no offscreen API", async () => {
    chromeWith({});
    await rejectsWith(() => ensureOffscreen("why"), "unsupported operation");
  });

  it("creates the document once, declaring all three reasons", async () => {
    const created: Any[] = [];
    chromeWith({
      offscreen: {
        Reason: OFFSCREEN_REASONS,
        createDocument: async (options: Any) => void created.push(options),
      },
    });
    await ensureOffscreen("recording");
    assert.equal(created.length, 1);
    assert.equal(created[0].url, "offscreen.html");
    assert.deepEqual(created[0].reasons, ["CLIPBOARD", "DISPLAY_MEDIA", "USER_MEDIA"]);
    assert.equal(created[0].justification, "recording");
  });

  it("reuses an existing document instead of creating a second one", async () => {
    const created: Any[] = [];
    chromeWith(
      {
        offscreen: {
          Reason: OFFSCREEN_REASONS,
          createDocument: async (options: Any) => void created.push(options),
        },
      },
      [{ contextType: "OFFSCREEN_DOCUMENT", documentUrl: "chrome-extension://id/offscreen.html" }],
    );
    await ensureOffscreen("recording");
    assert.equal(created.length, 0, "createDocument 对已存在的文档会抛，不能重复建");
  });
});

describe("offscreenDocuments", () => {
  it("reports only offscreen contexts", async () => {
    chromeWith({}, [
      { contextType: "SERVICE_WORKER", documentUrl: undefined },
      { contextType: "OFFSCREEN_DOCUMENT", documentUrl: "chrome-extension://id/offscreen.html" },
    ]);
    assert.deepEqual(await offscreenDocuments(), {
      documents: [{ documentUrl: "chrome-extension://id/offscreen.html" }],
    });
  });

  it("refuses when runtime.getContexts is missing", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => offscreenDocuments(), "unsupported operation");
  });
});

describe("pageCaptureSaveMhtml", () => {
  function tabContext(extra: Any, config: Any = DEFAULTS): void {
    chromeWith({
      tabs: {
        query: async () => [{ id: 7, url: "https://example.test/page", active: true }],
        get: async () => ({ id: 7, url: "https://example.test/page" }),
      },
      ...extra,
    }, [], config);
  }

  it("refuses when the browser has no pageCapture", async () => {
    tabContext({});
    await rejectsWith(() => pageCaptureSaveMhtml({}), "unsupported operation");
  });

  it("returns base64 without downloading when save is false", async () => {
    tabContext({
      pageCapture: { saveAsMHTML: async () => new Blob(["mhtml-body"]) },
      downloads: { download: async () => assert.fail("save=false 不该触发下载") },
    });
    const result = (await pageCaptureSaveMhtml({ save: false })) as Any;
    assert.equal(result.base64, Buffer.from("mhtml-body").toString("base64"));
    assert.equal(result.bytes, "mhtml-body".length);
  });

  it("downloads with a tab-derived filename by default", async () => {
    const downloads: Any[] = [];
    tabContext({
      pageCapture: { saveAsMHTML: async () => new Blob(["x"]) },
      downloads: {
        download: async (options: Any) => {
          downloads.push(options);
          return 42;
        },
      },
    });
    const result = (await pageCaptureSaveMhtml({})) as Any;
    assert.equal(result.download, 42);
    assert.equal(result.filename, "page-7.mhtml");
    assert.ok((downloads[0].url as string).startsWith("data:message/rfc822;base64,"));
  });

  it("falls back to base64 when the download is refused", async () => {
    tabContext({
      pageCapture: { saveAsMHTML: async () => new Blob(["x"]) },
      downloads: {
        download: async () => {
          throw new Error("data: URL too large");
        },
      },
    });
    const result = (await pageCaptureSaveMhtml({})) as Any;
    assert.equal(result.saved, false);
    assert.equal(result.base64, Buffer.from("x").toString("base64"));
  });

  it("reports an empty capture instead of encoding nothing", async () => {
    tabContext({ pageCapture: { saveAsMHTML: async () => undefined } });
    await rejectsWith(() => pageCaptureSaveMhtml({}), "unknown error");
  });

  it("propagates a refused confirmation", async () => {
    // 默认 confirm_mode 是 silent（直接放行），要测拒绝就得把模式调到 always
    tabContext(
      { pageCapture: { saveAsMHTML: async () => new Blob(["x"]) } },
      { ...DEFAULTS, confirm_mode: "always" },
    );
    setConfirmHook(async () => false);
    await rejectsWith(() => pageCaptureSaveMhtml({}), "lg:user rejected");
  });
});

describe("captureRecordStop", () => {
  it("needs the recording id from recordTab/recordDesktop", async () => {
    chromeWith({});
    await rejectsWith(() => captureRecordStop({}), "invalid argument");
    await rejectsWith(() => captureRecordStop({ recording: "" }), "invalid argument");
  });

  it("surfaces the offscreen document's own error", async () => {
    chromeWith({ runtime: { sendMessage: async () => ({ ok: false, error: "no such recording" }) } });
    const error = await rejectsWith(() => captureRecordStop({ recording: "rec-9" }), "unknown error");
    assert.match(error.message, /no such recording/);
  });

  it("still forwards the stop after the service worker forgot the recording", async () => {
    // active 只是缓存；SW 重启会失忆，而 offscreen 里的 MediaRecorder 还活着
    chromeWith({
      runtime: {
        sendMessage: async () => ({ ok: true, base64: "AAA", bytes: 3, kind: "tab", seconds: 2 }),
      },
    });
    const result = (await captureRecordStop({ recording: "rec-99", save: false })) as Any;
    assert.deepEqual(result, { base64: "AAA", bytes: 3, seconds: 2, kind: "tab" });
  });
});

describe("the record lifecycle", () => {
  it("records a tab, refuses a second one, and only stops its own id", async () => {
    const messages: Any[] = [];
    chromeWith({
      offscreen: { Reason: OFFSCREEN_REASONS, createDocument: async () => {} },
      tabCapture: { getMediaStreamId: async () => "stream-1" },
      tabs: {
        query: async () => [{ id: 3, url: "https://example.test/page", active: true }],
        get: async () => ({ id: 3, url: "https://example.test/page" }),
      },
      downloads: { download: async () => 1 },
      runtime: {
        getContexts: async () => [],
        getURL: (path: string) => path,
        sendMessage: async (message: Any) => {
          messages.push(message);
          return message.type === "lg:record-start"
            ? { ok: true }
            : { ok: true, base64: "QQ==", bytes: 1, kind: "tab", seconds: 5 };
        },
      },
    });

    const started = await captureRecordTab({});
    assert.equal(started.tab, 3);
    assert.match(started.recording, /^rec-\d+$/);
    assert.equal(messages[0].streamId, "stream-1");
    assert.equal(messages[0].kind, "tab");

    await rejectsWith(() => captureRecordTab({}), "invalid argument");
    await rejectsWith(() => captureRecordStop({ recording: "rec-not-mine" }), "invalid argument");

    const stopped = (await captureRecordStop({ recording: started.recording, save: false })) as Any;
    assert.equal(stopped.seconds, 5);
    // 停完 active 归零，下一路录制又能开起来
    const again = await captureRecordTab({});
    assert.notEqual(again.recording, started.recording);
    await captureRecordStop({ recording: again.recording, save: false });
  });
});

describe("captureRecordDesktop", () => {
  function desktopChrome(): void {
    chromeWith({
      desktopCapture: { chooseDesktopMedia: () => 0 },
      windows: { create: async () => ({ id: 1 }), remove: async () => {} },
      offscreen: { Reason: OFFSCREEN_REASONS, createDocument: async () => {} },
    });
  }

  it("rejects a sources list that is empty or has an unknown entry", async () => {
    desktopChrome();
    await rejectsWith(() => captureRecordDesktop({ sources: [] }), "invalid argument");
    await rejectsWith(() => captureRecordDesktop({ sources: ["webcam"] }), "invalid argument");
    await rejectsWith(() => captureRecordDesktop({ sources: "screen" }), "invalid argument");
  });

  it("rejects a timeout outside the 5–600 second window", async () => {
    desktopChrome();
    await rejectsWith(() => captureRecordDesktop({ timeout: 1 }), "invalid argument");
    await rejectsWith(() => captureRecordDesktop({ timeout: 601 }), "invalid argument");
    await rejectsWith(() => captureRecordDesktop({ timeout: "30" }), "invalid argument");
  });

  it("refuses when the browser has no desktopCapture", async () => {
    chromeWith({});
    await rejectsWith(() => captureRecordDesktop({}), "unsupported operation");
  });

  it("treats a picker that hands back nothing as a refusal", async () => {
    desktopChrome();
    const pending = captureRecordDesktop({});
    // 等 picker 窗口建好再回传：desktopSourcePicked 要有人在等才返回 true
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(desktopSourcePicked(undefined), true);
    await rejectsWith(() => pending, "lg:user rejected");
  });

  it("starts recording with the stream the picker returned", async () => {
    desktopChrome();
    const pending = captureRecordDesktop({ sources: ["window"] });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(desktopSourcePicked("stream-desktop"), true);
    const result = (await pending) as Any;
    assert.match(result.recording as string, /^rec-\d+$/);
    await captureRecordStop({ recording: result.recording as string, save: false });
  });
});

describe("desktopSourcePicked", () => {
  it("reports that nothing was waiting for a pick", () => {
    assert.equal(desktopSourcePicked("stream-x"), false);
  });
});
