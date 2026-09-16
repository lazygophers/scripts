/**
 * 2026-09-16 扩容面的单测：挑有真实分支的那些（校验、状态机、不支持路径），
 * 纯透传的（topSites、gcm 之类）由 E2E 覆盖。
 */
import assert from "node:assert/strict";
import test from "node:test";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";
import {
  captureRecordStop,
  captureRecordTab,
  pageCaptureSaveMhtml,
} from "../src/handlers/capture.ts";
import { clipboardRead, clipboardWrite } from "../src/handlers/clipboard.ts";
import { dnsResolve, idleState, searchQuery, systemInfo } from "../src/handlers/info.ts";
import { declContentSetRules } from "../src/handlers/scripts.ts";
import { proxySet } from "../src/handlers/proxy.ts";
import { wauthComplete } from "../src/handlers/wauth.ts";
import { printingRespond } from "../src/handlers/printing.ts";

type Any = Record<string, unknown>;

test.afterEach(() => clearChrome());

test("clipboard 没有 offscreen 权限就明确拒绝", async () => {
  installChrome({ runtime: { getContexts: async () => [] } });
  // offscreen 命名空间都没装：ensureOffscreen 的 requireApi 应该拦下
  await rejectsWith(() => clipboardRead(), "unsupported operation");
});

test("clipboard 读写经 offscreen 文档往返", async () => {
  const sent: Any[] = [];
  installChrome({
    offscreen: { Reason: { CLIPBOARD: "CLIPBOARD" }, createDocument: async () => {} },
    runtime: {
      getContexts: async () => [],
      sendMessage: async (message: Any) => {
        sent.push(message);
        return message.op === "read"
          ? { ok: true, text: "剪贴板内容" }
          : { ok: true };
      },
    },
  });
  assert.deepEqual(await clipboardRead(), { text: "剪贴板内容" });
  assert.deepEqual(await clipboardWrite({ text: "写入" }), { wrote: 2 });
  assert.equal(sent.filter((m) => m.type === "lg:clipboard").length, 2);
});

test("saveMhtml 的 save=false 回 base64，默认走 downloads", async () => {
  const blob = new Blob(["<html>mhtml</html>"], { type: "message/rfc822" });
  const downloads: Any[] = [];
  installChrome({
    pageCapture: { saveAsMHTML: async () => blob },
    downloads: {
      download: async (options: Any) => {
        downloads.push(options);
        return 42;
      },
    },
    // resolveContext 落到 context 7；targetUrl 再查一次 tabs.get
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/" }],
      get: async (id: number) => ({ id, url: "https://a.test/" }),
    },
  });
  const saved = await pageCaptureSaveMhtml({ context: "7" }) as Any;
  assert.equal(saved.download, 42);
  assert.match(String(downloads[0].url), /^data:message\/rfc822;base64,/);
  const inline = await pageCaptureSaveMhtml({ context: "7", save: false }) as Any;
  assert.match(inline.base64, /^[A-Za-z0-9+/]+={0,2}$/);
});

test("recordTab 启动失败回未知错误", async () => {
  installChrome({
    tabCapture: {
      getMediaStreamId: async () => "stream-1",
    },
    offscreen: { Reason: { DISPLAY_MEDIA: "DISPLAY_MEDIA" }, createDocument: async () => {} },
    runtime: {
      getContexts: async () => [],
      sendMessage: async () => ({ ok: false, error: "getUserMedia denied" }),
    },
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/" }],
      get: async (id: number) => ({ id, url: "https://a.test/" }),
    },
  });
  await rejectsWith(() => captureRecordTab({ context: "7" }), "unknown error");
});

test("recordStop 的 id 要对得上，且没人录时报 invalid argument", async () => {
  await rejectsWith(() => captureRecordStop({ recording: "rec-1" }), "invalid argument");
});

test("idle threshold 越界拒绝", async () => {
  installChrome({ idle: { queryState: async () => "active" } });
  await rejectsWith(() => idleState({ threshold: 5 }), "invalid argument");
  assert.deepEqual(await idleState({}), { state: "active" });
});

test("search disposition 白名单", async () => {
  installChrome({ search: { query: async () => {} } });
  await rejectsWith(() => searchQuery({ text: "a", disposition: "SIDeways" }), "invalid argument");
  await rejectsWith(() => searchQuery({}), "invalid argument");
});

test("dns/processes/system 没有对应 API 时明确拒绝", async () => {
  installChrome({});
  await rejectsWith(() => dnsResolve({ hostname: "a.test" }), "unsupported operation");
  await rejectsWith(() => systemInfo({ parts: ["cpu"] }), "unsupported operation");
  await rejectsWith(() => systemInfo({ parts: ["nope"] }), "invalid argument");
});

test("proxy.set 的模式与参数校验", async () => {
  const setCalls: Any[] = [];
  installChrome({
    proxy: { settings: { set: async (details: Any) => { setCalls.push(details); } } },
  });
  await rejectsWith(() => proxySet({ mode: "socks" }), "invalid argument");
  await rejectsWith(() => proxySet({ mode: "fixed_servers", host: "a" }), "invalid argument");
  await proxySet({ mode: "fixed_servers", host: "127.0.0.1", port: 7890 });
  assert.deepEqual(setCalls[0].value, {
    mode: "fixed_servers",
    rules: { singleProxy: { scheme: "http", host: "127.0.0.1", port: 7890 } },
  });
});

test("wauth.complete 只认 get/create + 数字状态码", async () => {
  installChrome({
    webAuthenticationProxy: {
      completeGetRequest: async () => {},
      completeCreateRequest: async () => {},
    },
  });
  await rejectsWith(() => wauthComplete({ request: "1", kind: "sign" }), "invalid argument");
  await rejectsWith(() => wauthComplete({ request: "1", kind: "get" }), "invalid argument");
  assert.deepEqual(await wauthComplete({
    request: "1", kind: "get", httpStatusCode: 200,
  }), { completed: "1" });
});

test("printing.respond 的状态白名单与不存在的请求", async () => {
  installChrome({ printerProvider: {} });
  await rejectsWith(() => printingRespond({ request: "p-1", status: "MAYBE" }), "invalid argument");
  await rejectsWith(() => printingRespond({ request: "p-1", status: "OK" }), "invalid argument");
});

test("declContent.setRules 每条规则都要有 url 前缀或 glob", async () => {
  installChrome({
    declarativeContent: {
      PageUrlMatcher: class {
        options: Any;
        constructor(options: Any) {
          this.options = options;
        }
      },
      ShowAction: class {},
      onPageChanged: {
        removeRules: async () => {},
        addRules: async () => {},
      },
    },
  });
  await rejectsWith(() => declContentSetRules({ rules: [{}] }), "invalid argument");
  const done = await declContentSetRules({
    rules: [{ urlPrefix: "https://a.test/" }, { urlMatches: ".*\\.test/.*" }],
  }) as Any;
  assert.equal(done.rules, 2);
});
