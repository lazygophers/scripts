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

function realm(reply: (message: Any) => Any, store: Any = {}): JSDOM {
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
        get: async (keys?: string | string[]) => {
          if (keys === undefined) return { ...store };
          const out: Any = {};
          for (const k of Array.isArray(keys) ? keys : [keys]) out[k] = store[k];
          return out;
        },
        set: async (items: Any) => void Object.assign(store, items),
        remove: async (keys: string | string[]) => {
          for (const k of Array.isArray(keys) ? keys : [keys]) delete store[k];
        },
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

type PanelModule = typeof import("../src/panel.ts");

let mod: PanelModule | undefined;

/**
 * 模块只导入一次（带 query 破缓存会让 node 的覆盖率归因整段丢失——函数体全部
 * 报成未覆盖）。自举段导出成 `bootstrap()`，换 realm 的用例自己重跑它。
 */
async function load(): Promise<PanelModule> {
  mod ??= await import("../src/panel.ts");
  mod.bootstrap();
  // 面板启动时的三次读取都是异步的，让它们落地
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  return mod;
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

test("免确认名单渲染成行，撤销后立刻消失且不再问 service worker", async () => {
  const store: Any = { "browse:config": { confirm_mode: "per_domain", approved_domains: ["a.test", "b.test"] } };
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {}, store);
  await load();
  const doc = dom.window.document;

  assert.match(doc.getElementById("status")?.textContent ?? "", /panelDomainCount:2/);
  const rows = [...doc.querySelectorAll("#list li")].map((li) => li.textContent ?? "");
  assert.ok(rows.some((r) => r?.includes("a.test")), rows.join(" | "));
  assert.ok(rows.every((r) => r?.includes("panelRevoke")));

  const before = asked.length;
  (doc.querySelector("#list li button") as HTMLButtonElement).click();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 1));
  assert.deepEqual((store["browse:config"] as Any).approved_domains, ["b.test"]);
  assert.ok(doc.querySelectorAll("#list li").length < rows.length);
  assert.equal(asked.length, before, "撤销走插件自己的存储，不经 service worker");
});

test("空名单显示「没有域名」", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();
  assert.match(dom.window.document.getElementById("status")?.textContent ?? "", /panelNoDomains/);
});

test("指令日志为空或筛选无命中时给出占位行", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();
  assert.match(dom.window.document.querySelector("#log li")?.textContent ?? "", /panelNothingRun/);

  const dom2 = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log"
        ? { entries: [{ at: 1_700_000_000_000, method: "input.click", ok: true, ms: 5, error: "" }], state: "connected" }
        : {});
  await load();
  const filter = dom2.window.document.getElementById("logFilter") as HTMLInputElement;
  filter.value = "nomatch-here";
  filter.dispatchEvent(new dom2.window.Event("input"));
  assert.match(dom2.window.document.querySelector("#log li")?.textContent ?? "", /panelNothingRun/);
});

test("stopped 状态与从未连过的状态各自有说法", async () => {
  const stopped = { ...INFO, status: { ...INFO.status, stopped: true } };
  const dom = realm((message) =>
    message.type === "browse-bridge" ? stopped
      : message.type === "browse-log" ? { entries: [], state: "" } : {});
  await load();
  assert.match(dom.window.document.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeStopped/);
  assert.match(dom.window.document.getElementById("logStatus")?.textContent ?? "", /panelDaemonDisconnected/);

  const never = { ok: false, status: { state: "disconnected", attempt: 0, stopped: false, reason: "ECONNREFUSED" } };
  const dom2 = realm((message) =>
    message.type === "browse-bridge" ? never
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();
  assert.match(dom2.window.document.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeDown:ECONNREFUSED/);
  // connectedAt=0（从未连过）→ 「—」而不是负数秒
  const rows = [...dom2.window.document.querySelectorAll("#bridge li")].map((li) => li.textContent ?? "");
  assert.ok(rows.some((r) => r?.includes("—")), rows.join(" | "));
});

test("服务端日志缺失时给出占位而不是空白", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge" ? { ...INFO, lines: undefined }
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();
  assert.match(dom.window.document.getElementById("serverLogStatus")?.textContent ?? "", /panelNoServerLog/);
});

test("service worker 不回话时错误文本落到状态行", async () => {
  const dom = realm(() => {
    throw new Error("port closed");
  });
  await load();
  assert.match(dom.window.document.getElementById("logStatus")?.textContent ?? "", /port closed/);
  assert.match(dom.window.document.getElementById("bridgeStatus")?.textContent ?? "", /port closed/);
});

test("断开按钮发 browse-disconnect 并重读两块日志", async () => {
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  await load();
  const before = asked.length;
  (dom.window.document.getElementById("cut") as HTMLButtonElement).click();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 1));
  assert.ok(asked.some((m) => m.type === "browse-disconnect"), JSON.stringify(asked));
  assert.ok(asked.length >= before + 3, "断开后要把日志和 bridge 两块都重读");
  assert.match(dom.window.document.getElementById("cut")?.textContent ?? "", /panelDisconnected/);
  assert.equal((dom.window.document.getElementById("cut") as HTMLButtonElement).disabled, true);
});

test("设置按钮直达扩展设置页", async () => {
  let opened = 0;
  const dom = realm((message) =>
    message.type === "browse-bridge" ? INFO
      : message.type === "browse-log" ? { entries: [], state: "connected" } : {});
  (globalThis.chrome as Any).runtime.openOptionsPage = () => { opened += 1; };
  await load();
  (dom.window.document.getElementById("settings") as HTMLElement).click();
  assert.equal(opened, 1);
});
