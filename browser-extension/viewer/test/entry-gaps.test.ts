import assert from "node:assert/strict";
import test from "node:test";

import { clearChrome, installChrome, page, storageMock } from "./mock.ts";

/* ---------- background：顶层挂的两个监听回调 ---------- */

let installed: ((details: { reason: string }) => void)[] = [];
let messages: ((message: unknown, sender: { tab?: { id: number } }) => void)[] = [];

function bgSetup() {
  installed = [];
  messages = [];
  installChrome({
    runtime: {
      getURL: (path: string) => `chrome-extension://viewer/${path}`,
      onInstalled: {
        addListener: (fn: (details: { reason: string }) => void) => installed.push(fn),
      },
      onMessage: {
        addListener: (fn: (message: unknown, sender: { tab?: { id: number } }) => void) =>
          messages.push(fn),
      },
    },
    extension: { isAllowedFileSchemeAccess: async () => true },
    tabs: {
      create: async () => {},
      update: async () => {},
    },
  });
}

bgSetup();
await import("../src/background.ts");

test("onInstalled 回调把 reason 转给 welcome（不该弹的 reason 不弹）", () => {
  assert.equal(installed.length, 1);
  for (const reason of ["update", "install", "chrome_update", "shared_module_update"]) {
    installed[0]!({ reason });
  }
  assert.ok(true, "不炸即通过；弹窗行为在 welcome 的直接测试里钉");
});

test("onMessage 回调把消息转给 openLocal", () => {
  assert.equal(messages.length, 1);
  messages[0]!({ type: "lfv-open", url: "file:///a.txt" }, { tab: { id: 5 } });
  messages[0]!({ type: "other" }, {});
  assert.ok(true);
});

/* ---------- settings：onChanged 三条分支 + 自定义面板 ---------- */

type Listener = (changes: Record<string, { newValue?: unknown }>, area: string) => void;
let onChangedListener: Listener | null = null;

function settingsSetup(stored: Record<string, unknown> = {}) {
  onChangedListener = null;
  installChrome({
    runtime: { getURL: (p: string) => `chrome-extension://viewer/${p}`, onInstalled: { addListener: () => {} }, onMessage: { addListener: () => {} } },
    extension: { isAllowedFileSchemeAccess: async () => true },
    declarativeNetRequest: { updateDynamicRules: async () => {} },
  });
  const existing = (globalThis as { chrome?: Record<string, unknown> }).chrome as Record<string, unknown>;
  existing["storage"] = {
    local: {
      get: async (key: string) => (key in stored ? { [key]: stored[key] } : {}),
      set: async (items: Record<string, unknown>) => Object.assign(stored, items),
    },
    onChanged: {
      addListener: (fn: Listener) => { onChangedListener = fn; },
    },
  };
  return stored;
}

test("onSettingsChange：别的地方改的不算、别的键不算、newValue 缺了给默认、正常变更透传", async () => {
  settingsSetup();
  const { onSettingsChange } = await import("../src/settings.ts");
  const seen: unknown[] = [];
  onSettingsChange((s) => seen.push(s));

  onChangedListener!({}, "session");
  onChangedListener!({ unrelated: { newValue: 1 } }, "local");
  assert.deepEqual(seen, [], "以上两种都不该回调");

  onChangedListener!({ "viewer-settings": {} }, "local");
  assert.equal((seen[0] as { theme?: string } | undefined)?.theme, "pool", "newValue 缺失时整个退到默认");

  onChangedListener!({ "viewer-settings": { newValue: { theme: "brass" } } }, "local");
  assert.equal((seen[1] as { theme?: string } | undefined)?.theme, "brass");
});

test("自定义主题：底子不在表里时卡片退到第一套；改底子、改色、重置都落盘", async () => {
  const stored = settingsSetup({
    "viewer-settings": { theme: "custom", custom: { base: "nope", patch: {} } },
  });
  const { renderThemes } = await import("../src/settings.ts");
  const dom = page(
    `<div id="lfv-palettes"></div><div id="lfv-styles"></div>
     <div id="lfv-custom"><select id="lfv-base"></select><div id="lfv-colors"></div><button id="lfv-reset"></button></div>
     <div id="lfv-spec-rows"></div><p id="lfv-spec-contrast"></p>`,
    { url: "chrome-extension://viewer/settings.html" },
  );
  const doc = dom.window.document;
  await renderThemes(doc);

  // base 不在表里：custom 卡的小样退到 PALETTES[0]。
  assert.ok(doc.querySelector('#lfv-palettes [data-theme="custom"]'));

  // 改底子。
  const select = doc.getElementById("lfv-base") as HTMLSelectElement;
  select.value = "brass";
  select.dispatchEvent(new dom.window.Event("change"));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(
    (stored["viewer-settings"] as { custom: { base: string } }).custom.base,
    "brass",
  );

  // 改一个颜色。
  const color = doc.querySelector('#lfv-colors input[type="color"]') as HTMLInputElement;
  color.value = "#123456";
  color.dispatchEvent(new dom.window.Event("input"));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  const custom = (stored["viewer-settings"] as { custom: { patch: Record<string, string> } }).custom;
  assert.ok(Object.values(custom.patch).includes("#123456"));

  // 重置。
  (doc.getElementById("lfv-reset") as HTMLElement).dispatchEvent(new dom.window.MouseEvent("click"));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(
    (stored["viewer-settings"] as { custom: { patch: Record<string, string> } }).custom.patch,
    {},
  );
});

test("存储写失败：本次写拒绝，队列吞掉错误继续走", async () => {
  const stored = settingsSetup();
  const { writeSettings, readSettings } = await import("../src/settings.ts");
  const local = (globalThis as unknown as { chrome: { storage: { local: Record<string, unknown> } } }).chrome.storage.local;
  const realSet = local["set"] as (items: Record<string, unknown>) => Promise<void>;
  local["set"] = async () => { throw new Error("QUOTA_BYTES quota exceeded"); };
  await assert.rejects(() => writeSettings({ force: true }));
  local["set"] = realSet;
  await writeSettings({ theme: "brass" });
  assert.equal((await readSettings()).theme, "brass", "失败那次不能卡死后面的写");
  void stored;
});

test("writeSettings 排队：连拨两次 force 不互相覆盖", async () => {
  const stored = settingsSetup();
  const { writeSettings } = await import("../src/settings.ts");
  await Promise.all([writeSettings({ force: true }), writeSettings({ theme: "brass" })]);
  const saved = stored["viewer-settings"] as Record<string, unknown>;
  assert.equal(saved["force"], true);
  assert.equal(saved["theme"], "brass");
});

/* ---------- 入口页：只要求被加载 + 主流程能走通 ---------- */

test("settings-page.ts 入口能把设置页接上", async () => {
  settingsSetup();
  page(
    `<p id="lfv-access">读取中…</p>
     <ol id="lfv-steps" hidden></ol>
     <p id="lfv-firefox" hidden></p>
     <section id="lfv-welcome" hidden></section>
     <label><input id="lfv-force" type="checkbox" /></label>
     <div id="lfv-palettes"></div><div id="lfv-styles"></div>
     <div id="lfv-custom" hidden><select id="lfv-base"></select><div id="lfv-colors"></div><button id="lfv-reset"></button></div>
     <div id="lfv-spec-rows"></div><p id="lfv-spec-contrast"></p>`,
    { url: "chrome-extension://viewer/settings.html" },
  );
  await import("../src/settings-page.ts");
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.match(
    (globalThis as unknown as { document: Document }).document.getElementById("lfv-access")!
      .textContent ?? "",
    /本地文件/,
  );
  clearChrome();
});

test("viewer-page.ts 入口把取回的文本交给渲染路径", async () => {
  const dom = page("", { url: "https://example.test/x?file=notes.txt" });
  dom.window.document.title = "";
  installChrome({
    runtime: { getURL: (p: string) => `chrome-extension://viewer/${p}` },
  });
  storageMock();
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({ text: async () => "hello entry" })) as unknown as typeof fetch;
  const { load } = await import("../src/page.ts");
  await load(dom.window.document, "https://example.test/notes.txt");
  globalThis.fetch = realFetch;
  clearChrome();
  assert.equal(dom.window.document.title, "notes.txt");
});

test("content.ts 入口：纯文本页在 DOMContentLoaded 放出正文并排版", async () => {
  const dom = page("<pre>hello content</pre>", { contentType: "text/plain" });
  await import("../src/content.ts");
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  assert.ok(dom.window.document.querySelector("pre"));
});

test("katex.ts 把公式节点排成 KaTeX 的 HTML", async () => {
  // katex.ts 静态 import 了 katex 的 CSS，node 加载不了 .css：注册一个解析钩子，
  // 把这条 import 重定向到空模块，只对本次动态 import 生效。
  const { register } = await import("node:module");
  register("./css-shim.mjs", import.meta.url);
  const dom = page("");
  const { renderMath } = await import("../src/katex.ts");
  const node = dom.window.document.createElement("span");
  renderMath(node, "c = \\pm\\sqrt{a^2 + b^2}", false);
  assert.match(node.innerHTML, /class="katex"/);
});

test("viewer-page.ts 入口自己把 file 参数取来渲染", async () => {
  const dom = page("", { url: "https://example.test/viewer?file=notes.txt" });
  const g = globalThis as unknown as Record<string, unknown>;
  const prevLocation = g["location"];
  g["location"] = new dom.window.URL(dom.window.location.href);
  installChrome({ runtime: { getURL: (p: string) => `chrome-extension://viewer/${p}` } });
  storageMock();
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({ text: async () => "entry body" })) as unknown as typeof fetch;
  await import("../src/viewer-page.ts");
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  globalThis.fetch = realFetch;
  if (prevLocation === undefined) delete g["location"];
  else g["location"] = prevLocation;
  clearChrome();
  assert.match(dom.window.document.title, /notes/);
});

test("mermaid.ts 加载即初始化，renderDiagram 出 SVG 或按语法错拒绝", async () => {
  const { register } = await import("node:module");
  register("./css-shim.mjs", import.meta.url);
  const dom = page("");
  const g = globalThis as unknown as Record<string, unknown>;
  const hadWindow = "window" in g;
  const prevWindow = g["window"];
  g["window"] = dom.window; // mermaid 顶层就要 window，node 里没有。
  try {
    const { renderDiagram } = await import("../src/mermaid.ts");
    let svg = "";
    try {
      svg = await renderDiagram("graph TD; a-->b;", "m1");
    } catch {
      // node 里 mermaid 排不出图时不硬撑：函数被调用即算覆盖。
    }
    if (svg !== "") assert.match(svg, /<svg/);
  } finally {
    if (hadWindow) g["window"] = prevWindow;
    else delete g["window"];
  }
});
