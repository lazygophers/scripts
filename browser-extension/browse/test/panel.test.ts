/**
 * 面板。测的还是「能做歪的地方」：
 *
 * - bridge 没连上时，面板必须仍然把「为什么连不上」摆出来 —— 这正是用户这时唯一
 *   要看的东西，而它恰恰是最容易被一个 `throw` 吞掉的路径
 * - 服务端日志是 bridge 给的原始字段，面板不能因为多了个没见过的字段就崩
 * - 指令日志的筛选和复制只动显示，不重新去问 service worker
 *
 * panel.ts 没有导出，一切靠导入时自举。Node 的覆盖率不聚合带 query 的多次导入，
 * 所以整份测试共用**一个**模块实例：首个 realm 连着数据导入，之后的场景换 realm 后
 * 点 cut 按钮（它重跑 loadLog + loadBridge）来重触发。
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

/**
 * 装一个 realm：jsdom + 假 chrome。返回假 storage 的内容（revoke 用例要改它）。
 */
function realm(
  reply: (message: Any) => Any,
  opts: { storage?: Any; failSendMessage?: string } = {},
): { store: Any; dom: JSDOM } {
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
  let copied = "";
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text: string) => { copied = text; } },
  });
  asked = [];
  const store: Any = { ...(opts.storage ?? {}) };
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string, args?: string[]) =>
        `[${key}${args?.length ? ":" + args.join(",") : ""}]`,
    },
    runtime: {
      sendMessage: async (message: Any) => {
        if (opts.failSendMessage !== undefined) {
          throw new Error(opts.failSendMessage);
        }
        asked.push(message);
        return reply(message);
      },
      openOptionsPage: () => {},
    },
    storage: {
      local: {
        get: async () => store,
        set: async (items: Any) => void Object.assign(store, items),
        remove: async () => {},
      },
    },
  };
  (dom.window as unknown as Any).copied = () => copied;
  return { store, dom };
}

afterEach(() => {
  const g = globalThis as Any;
  delete g.chrome;
  delete g.document;
  delete g.HTMLElement;
  delete g.navigator;
});

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
}

let loaded = false;
/** cut 按钮的监听器闭包引用的是首个 realm 的节点，换 realm 后靠它重触发。 */
let cutBtn: HTMLButtonElement | null = null;
let settingsBtn: HTMLElement | null = null;

async function load(): Promise<void> {
  if (!loaded) {
    await import("../src/panel.ts");
    loaded = true;
    cutBtn = document.getElementById("cut") as HTMLButtonElement | null;
    settingsBtn = document.getElementById("settings");
  }
  await settle();
}

/**
 * panel.ts 把节点引用捕获在模块级常量里：换 realm 后点 cut，重跑的 loadLog/loadBridge
 * 画的还是首个 realm 的那份 DOM（已脱离文档但引用活着）。所以这里只换 chrome 应答，
 * 断言始终读首个 realm 的 document。
 */
let bootDoc: Document | null = null;

async function rerun(reply: (message: Any) => Any): Promise<Document> {
  realm(reply);
  // cut 点一次就自禁用，重触发前先恢复
  if (cutBtn !== null) {
    cutBtn.disabled = false;
  }
  cutBtn?.click();
  await settle();
  assert.ok(bootDoc !== null);
  return bootDoc;
}

const INFO = {
  ok: true,
  status: { state: "connected", url: "ws://127.0.0.1:9330", attempt: 0, stopped: false,
            connectedAt: Date.now() - 5_000, lastMessageAt: Date.now() - 1_000, reason: "" },
  info: { pid: 42, port: 9330, uptimeSeconds: 12, logPath: "/tmp/browse-bridge.log",
          connections: [{ browser: "chrome", idleSeconds: 3 }] },
  lines: [{ at: 1_700_000_000, event: "ws.open", browser: "chrome" }],
};

const LOG_ENTRIES = [
  { at: 1_700_000_000_000, method: "script.evaluate", ok: true, ms: 12, error: "" },
  { at: 1_700_000_001_000, method: "input.click", ok: false, ms: 30, error: "no such element" },
];

function richReply(message: Any): Any {
  return message.type === "browse-bridge" ? INFO
    : message.type === "browse-log" ? { entries: LOG_ENTRIES, state: "connected" } : {};
}

test("连上时列出进程、端口、运行时长、日志、复制与筛选、免确认名单", async () => {
  const { store, dom } = realm(richReply, {
    storage: { "browse:config": { confirm_mode: "per_domain", approved_domains: ["a.example", "b.example"] } },
  });
  await load();
  bootDoc = dom.window.document;
  const doc = dom.window.document;

  // bridge 上半截 + 下半截
  const rows = [...doc.querySelectorAll("#bridge li")].map((li) => li.textContent);
  assert.ok(rows.some((row) => row?.includes("pid 42")), rows.join(" | "));
  assert.ok(rows.some((row) => row?.includes("12s")));
  assert.ok(rows.some((row) => row?.includes("chrome")));
  assert.ok(rows.some((row) => row?.includes("/tmp/browse-bridge.log")));
  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeUp/);
  assert.match(doc.getElementById("serverLogStatus")?.textContent ?? "", /panelDomainCount:1/);
  assert.match(doc.querySelector("#serverLog li")?.textContent ?? "", /ws\.open/);

  // 指令日志：时间和耗时，筛选只改显示
  assert.equal(doc.querySelectorAll("#log li").length, 2);
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

  // 复制：一行一条纯文本，不含载荷
  (doc.getElementById("logCopy") as HTMLButtonElement).click();
  await settle();
  const text = (dom.window as unknown as { copied: () => string }).copied();
  assert.match(text, /ok script\.evaluate 12ms/);
  assert.match(text, /fail input\.click 30ms no such element/);
  assert.equal(text.split("\n").length, 2);

  // 免确认名单摆出来
  assert.match(doc.getElementById("status")?.textContent ?? "", /panelDomainCount:2/);
  const buttons = [...doc.querySelectorAll("#list button")] as HTMLButtonElement[];
  assert.ok(buttons.length === 2, "每个域名一个撤销按钮");
  buttons[0]?.click();
  await settle();
  assert.deepEqual(
    (store["browse:config"] as Any).approved_domains as string[],
    ["b.example"],
    "撤销要真的写存储",
  );
  assert.match(doc.getElementById("status")?.textContent ?? "", /panelDomainCount:1/);

  // settings 按钮在（点击行为不抛即可；openOptionsPage 是空实现）
  assert.ok(settingsBtn !== null);
});

test("掉线重试中、服务问不到：报重试次数和 unreachable，而不是一片空白", async () => {
  const down = {
    ok: false,
    status: { state: "disconnected", url: "ws://127.0.0.1:9330", attempt: 3, stopped: false,
              connectedAt: 0, lastMessageAt: 0, reason: "bridge 连接断开" },
    error: "bridge not connected",
  };
  const doc = await rerun((message) =>
    message.type === "browse-bridge" ? down
      : message.type === "browse-log" ? { entries: [], state: "disconnected" } : {});

  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeRetrying:3/);
  const rows = [...doc.querySelectorAll("#bridge li")].map((li) => li.textContent);
  assert.ok(rows.some((row) => row?.includes("bridge not connected")), rows.join(" | "));
  assert.match(doc.getElementById("logStatus")?.textContent ?? "", /panelDaemonDisconnected/);
  assert.match(doc.querySelector("#log li")?.textContent ?? "", /panelNothingRun/);
  assert.match(doc.getElementById("serverLogStatus")?.textContent ?? "", /panelNoServerLog/);
});

test("bridge 停了就明说；没在重试就给原因；没有时间戳的日志条目照样摆", async () => {
  const doc = await rerun((message) =>
    message.type === "browse-bridge"
      ? {
          ok: false,
          status: { stopped: true, state: "stopped", attempt: 0, reason: "" },
          lines: [{ event: "ws.note" }, { at: "not-a-number", event: "ws.other" }],
        }
      : { entries: [], state: "connected" },
  );
  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeStopped/);
  assert.match(doc.getElementById("logStatus")?.textContent ?? "", /panelDaemonConnected/);
  const rows = [...doc.querySelectorAll("#serverLog li")].map((li) => li.textContent ?? "");
  assert.match(rows[0] ?? "", /ws\.note/);
  assert.match(rows[1] ?? "", /ws\.other/);

  // 换一个掉线且没重试的答复，点 cut 重拉
  const g = globalThis as Any;
  g.chrome.runtime.sendMessage = async (message: Any) =>
    message.type === "browse-bridge"
      ? { ok: false, status: { state: "disconnected", attempt: 0, reason: "boom" } }
      : { entries: [], state: "disconnected" };
  if (cutBtn !== null) cutBtn.disabled = false;
  cutBtn?.click();
  await settle();
  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeDown:boom/);
});

test("sendMessage 抛错：日志和 bridge 两块都显示错误而不是空白", async () => {
  const doc = await rerun((message) => {
    if (message.type === "browse-disconnect") {
      return {};
    }
    throw new Error("sw down");
  });
  assert.match(doc.getElementById("logStatus")?.textContent ?? "", /sw down/);
  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /sw down/);

  // cut 之后恢复应答：重拉成功路径也再走一遍
  const g = globalThis as Any;
  g.chrome.runtime.sendMessage = async (message: Any) => richReply(message);
  if (cutBtn !== null) cutBtn.disabled = false;
  // 测试 1 在筛选框里留了 "click"，先清掉
  const filterNode = doc.getElementById("logFilter") as HTMLInputElement | null;
  if (filterNode !== null) {
    filterNode.value = "";
  }
  cutBtn?.click();
  await settle();
  assert.match(doc.getElementById("bridgeStatus")?.textContent ?? "", /panelBridgeUp/);
  assert.equal(doc.querySelectorAll("#log li").length, 2);
});

test("撤销写失败时错误摆到状态行，不炸面板", async () => {
  // 先恢复 chrome（上一轮 afterEach 清掉了），再让存储的 set 抛错
  const doc = await rerun(richReply);
  const g = globalThis as Any;
  g.chrome.storage.local.set = async () => {
    throw new Error("set boom");
  };
  // 名单来自首启的 boot realm；重画一份带免确认域名的答复
  g.chrome.runtime.sendMessage = async (message: Any) =>
    message.type === "browse-bridge"
      ? { ok: true, status: { state: "connected", attempt: 0, stopped: false } }
      : { entries: [], state: "connected" };
  if (cutBtn !== null) cutBtn.disabled = false;
  cutBtn?.click();
  await settle();
  const buttons = [...doc.querySelectorAll("#list button")] as HTMLButtonElement[];
  buttons[0]?.click();
  await settle();
  assert.match(doc.getElementById("status")?.textContent ?? "", /set boom/);
});
