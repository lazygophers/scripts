/**
 * 面板上的 bridge 那两块。测的还是「能做歪的地方」：
 *
 * - bridge 没连上时，面板必须仍然把「为什么连不上」摆出来 —— 这正是用户这时唯一
 *   要看的东西，而它恰恰是最容易被一个 `throw` 吞掉的路径
 * - 服务端日志是 bridge 给的原始字段，面板不能因为多了个没见过的字段就崩
 * - 指令日志的筛选和复制只动显示，不重新去问 service worker
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

const SRC = join(import.meta.dirname, "..", "src");
const HTML = readFileSync(join(SRC, "panel.html"), "utf8");

type Any = Record<string, unknown>;

/** 面板真正的 DOM：从 `panel.html` 里取 `<body>`，省得测试和页面各写一份结构。 */
function body(): string {
  return /<body[^>]*>([\s\S]*)<script/.exec(HTML)?.[1] ?? "";
}

/** 本轮发给 service worker 的消息。面板不该为了重画而多问一次。 */
let asked: Any[] = [];

function realm(reply: (message: Any) => Any): JSDOM {
  const dom = new JSDOM(`<!doctype html><body>${body()}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  // node 22 的 globalThis.navigator 只有 getter，直接赋值会抛；面板要用剪贴板，
  // 所以整个换掉这个属性。
  Object.defineProperty(g, "navigator", {
    configurable: true,
    value: dom.window.navigator,
  });
  asked = [];
  let copied = "";
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text: string) => { copied = text; } },
  });
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string, args?: string[]) =>
        `[${key}${args?.length ? ":" + args.join(",") : ""}]`,
    },
    runtime: {
      sendMessage: async (message: Any) => {
        asked.push(message);
        return reply(message);
      },
      openOptionsPage: () => {},
    },
    storage: {
      local: {
        get: async () => ({}),
        set: async () => {},
        remove: async () => {},
      },
    },
  };
  (dom.window as unknown as Any).copied = () => copied;
  return dom;
}

afterEach(() => {
  const g = globalThis as Any;
  delete g.chrome;
  delete g.document;
  delete g.HTMLElement;
  delete g.navigator;
});

/** 面板的模块级副作用只跑一次，所以每个用例自己导入一份新鲜的。 */
async function load(): Promise<void> {
  await import(`../src/panel.ts?${Math.random()}`);
  // 面板启动时的三次读取都是异步的，让它们落地
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
}

const INFO = {
  ok: true,
  status: { state: "connected", url: "ws://127.0.0.1:9330", attempt: 0, stopped: false,
            connectedAt: Date.now() - 5_000, lastMessageAt: Date.now() - 1_000, reason: "" },
  info: { pid: 42, port: 9330, uptimeSeconds: 12, logPath: "/tmp/browse-bridge.log",
          connections: [{ browser: "chrome", idleSeconds: 3 }] },
  lines: [{ at: 1_700_000_000, event: "ws.open", browser: "chrome" }],
};

test("连上时列出进程、端口、已运行时长和每条浏览器连接", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();

  const rows = [...dom.window.document.querySelectorAll("#bridge li")].map((li) => li.textContent);
  assert.ok(rows.some((row) => row?.includes("pid 42")), rows.join(" | "));
  assert.ok(rows.some((row) => row?.includes("12s")));
  assert.ok(rows.some((row) => row?.includes("chrome")));
  assert.ok(rows.some((row) => row?.includes("/tmp/browse-bridge.log")));
  assert.match(dom.window.document.getElementById("bridgeStatus")?.textContent ?? "",
               /panelBridgeUp/);
});

test("bridge 问不到时仍然报出连接近况和原因，而不是一片空白", async () => {
  const down = {
    ok: false,
    status: { state: "disconnected", url: "ws://127.0.0.1:9330", attempt: 3, stopped: false,
              connectedAt: 0, lastMessageAt: 0, reason: "bridge 连接断开" },
    error: "bridge not connected",
  };
  const dom = realm((message) =>
    message.type === "browse-bridge" ? down
      : message.type === "browse-log" ? { entries: [], state: "disconnected" } : {});
  await load();

  assert.match(dom.window.document.getElementById("bridgeStatus")?.textContent ?? "",
               /panelBridgeRetrying:3/);
  const rows = [...dom.window.document.querySelectorAll("#bridge li")].map((li) => li.textContent);
  assert.ok(rows.some((row) => row?.includes("bridge not connected")), rows.join(" | "));
});

test("服务端日志逐条摆出来，多出来的字段也照样显示", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge"
      ? { ...INFO, lines: [{ at: 1_700_000_000, event: "ws.dropped", reason: "WsClosed", detail: "x" }] }
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();

  const row = dom.window.document.querySelector("#serverLog li")?.textContent ?? "";
  assert.match(row, /ws\.dropped/);
  assert.match(row, /reason=WsClosed/);
  assert.match(row, /detail=x/);
});

test("指令日志显示时间和耗时，筛选只改显示不再去问一次", async () => {
  const entries = [
    { at: 1_700_000_000_000, method: "script.evaluate", ok: true, ms: 12, error: "" },
    { at: 1_700_000_001_000, method: "input.click", ok: false, ms: 30, error: "no such element" },
  ];
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries, state: "connected" } : {});
  await load();
  const doc = dom.window.document;

  assert.equal(doc.querySelectorAll("#log li").length, 2);
  assert.ok(doc.querySelector("#log li")?.textContent?.includes("12ms"));
  assert.ok(doc.querySelector("#log li .at")?.textContent?.length === 8);

  const before = asked.length;
  const filter = doc.getElementById("logFilter") as HTMLInputElement;
  filter.value = "click";
  filter.dispatchEvent(new dom.window.Event("input"));

  const shown = [...doc.querySelectorAll("#log li")].map((li) => li.textContent ?? "");
  assert.equal(shown.length, 1);
  assert.match(shown[0] ?? "", /input\.click/);
  assert.match(shown[0] ?? "", /no such element/);
  assert.equal(asked.length, before, "筛选不该再问一次 service worker");
});

test("复制给出一行一条的纯文本，不含任何载荷", async () => {
  const entries = [
    { at: 1_700_000_000_000, method: "script.evaluate", ok: true, ms: 12, error: "" },
    { at: 1_700_000_001_000, method: "input.click", ok: false, ms: 30, error: "no such element" },
  ];
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries, state: "connected" } : {});
  await load();

  (dom.window.document.getElementById("logCopy") as HTMLButtonElement).click();
  await new Promise((resolve) => setTimeout(resolve, 5));

  const text = (dom.window as unknown as { copied: () => string }).copied();
  assert.match(text, /ok script\.evaluate 12ms/);
  assert.match(text, /fail input\.click 30ms no such element/);
  assert.equal(text.split("\n").length, 2);
});
