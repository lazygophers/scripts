import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";

import { clearChrome, installChrome, page, storageMock } from "./mock.ts";
import { fileAccess, readSettings, renderSettings, writeSettings } from "../src/settings.ts";
import { fileOf, load } from "../src/page.ts";
import { rules } from "../src/rules.ts";

/** 本轮开的标签页。欢迎页和展示页都是这么开出来的。 */
let opened: string[] = [];

/** 本轮写进浏览器的拦截规则。开关拨一次写一次。 */
let written: { removeRuleIds: number[]; addRules: { condition: { regexFilter: string } }[] }[] = [];

/**
 * 装一套假的 chrome。`allowed` 是浏览器里「允许访问文件网址」那个开关的状态。
 */
function setup({ allowed = true, stored = {} as Record<string, unknown> } = {}) {
  opened = [];
  written = [];
  installChrome({
    runtime: {
      getURL: (path: string) =>
        // 懒加载的包在浏览器里是 dist 产物，测试里指回源码，`import()` 才真的加载得动。
        path === "data.js"
          ? new URL("../src/data.ts", import.meta.url).href
          : `chrome-extension://viewer/${path}`,
      onInstalled: { addListener: () => {} },
      onMessage: { addListener: () => {} },
    },
    extension: { isAllowedFileSchemeAccess: async () => allowed },
    tabs: {
      create: async ({ url }: { url: string }) => {
        opened.push(url);
      },
    },
    declarativeNetRequest: {
      updateDynamicRules: async (change: (typeof written)[number]) => {
        written.push(change);
      },
    },
  });
  return storageMock(stored);
}

// `background.ts` 一加载就去挂浏览器的事件监听，所以先把假 chrome 装上再动态加载它。
setup();
const { welcome, openLocal } = await import("../src/background.ts");

const realFetch = globalThis.fetch;

afterEach(() => {
  clearChrome();
  globalThis.fetch = realFetch;
});

beforeEach(() => {
  setup();
});

/** 设置页的骨架。id 和 `src/settings.html` 里一模一样，页面上怎么长这里就怎么长。 */
function settingsPage(url = "chrome-extension://viewer/settings.html") {
  return page(
    `<section id="lfv-welcome" hidden></section>
     <p id="lfv-access">读取中…</p>
     <ol id="lfv-steps" hidden></ol>
     <p id="lfv-firefox" hidden></p>
     <label><input id="lfv-force" type="checkbox" /></label>
     <div id="lfv-themes"></div>
     <div id="lfv-custom" hidden>
       <select id="lfv-base"></select>
       <div id="lfv-colors"></div>
       <div id="lfv-type"></div>
       <button id="lfv-reset"></button>
     </div>`,
    { url },
  );
}

test("设置默认是关的，改了之后存得住", async () => {
  const store = setup();

  const base = { force: false, theme: "night", custom: { base: "night", patch: {} } };
  assert.deepEqual(await readSettings(), base);

  await writeSettings({ force: true });

  // 存的是扩展自己的存储，浏览器重启照样在——这里用「换一个新进程再读一次」模拟。
  assert.deepEqual(await readSettings(), { ...base, force: true });
  assert.deepEqual(store.data["viewer-settings"], { ...base, force: true });
});

test("设置页显示文件访问权限的真实状态", async () => {
  setup({ allowed: true });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const status = doc.getElementById("lfv-access") as HTMLElement;
  assert.equal(status.dataset["state"], "on");
  assert.equal(status.textContent, "已经打开，本地文件可以正常显示");
  // 已经开着的用户不必再看那串步骤。
  assert.equal((doc.getElementById("lfv-steps") as HTMLElement).hidden, true);
  assert.equal(await fileAccess(), true);
});

test("权限没开时摊开怎么去打开的步骤", async () => {
  setup({ allowed: false });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const status = doc.getElementById("lfv-access") as HTMLElement;
  assert.equal(status.dataset["state"], "off");
  assert.equal(status.textContent, "还没打开，本地文件显示不了");
  assert.equal((doc.getElementById("lfv-steps") as HTMLElement).hidden, false);
});

test("地址带 welcome 才多出那句欢迎话", async () => {
  setup();
  const hello = (url: string) => {
    const doc = settingsPage(url).window.document;
    return renderSettings(doc).then(
      () => (doc.getElementById("lfv-welcome") as HTMLElement).hidden,
    );
  };

  assert.equal(await hello("chrome-extension://viewer/settings.html?welcome=1"), false);
  assert.equal(await hello("chrome-extension://viewer/settings.html"), true);
});

test("设置页上的强制拦截开关读存储、也写存储", async () => {
  const store = setup({ stored: { "viewer-settings": { force: true } } });
  const dom = settingsPage();
  const doc = dom.window.document;
  await renderSettings(doc);

  const box = doc.getElementById("lfv-force") as HTMLInputElement;
  assert.equal(box.checked, true);

  box.checked = false;
  box.dispatchEvent(new dom.window.Event("change"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal((store.data["viewer-settings"] as { force: boolean }).force, false);
});

test("装好扩展时权限没开就弹欢迎页", async () => {
  setup({ allowed: false });

  assert.equal(await welcome("install"), true);
  assert.deepEqual(opened, ["chrome-extension://viewer/settings.html?welcome=1"]);
});

test("权限已经开着的用户装完不被打扰，升级也不弹", async () => {
  setup({ allowed: true });
  assert.equal(await welcome("install"), false);

  setup({ allowed: false });
  assert.equal(await welcome("update"), false);
  assert.deepEqual(opened, []);
});

test("开关打开后写进拦截规则，关掉后又撤干净", async () => {
  const store = setup();

  await writeSettings({ force: true });
  const on = written[0] as (typeof written)[number];
  assert.deepEqual(
    on.addRules.map((rule) => rule.condition.regexFilter),
    ["^file:///.*\\.yaml$", "^file:///.*\\.yml$", "^file:///.*\\.csv$"],
  );

  await writeSettings({ force: false });
  const off = written[1] as (typeof written)[number];
  // 关掉就是把同一批 id 删掉，规则存在浏览器里，不删干净就不算恢复。
  assert.deepEqual(off.addRules, []);
  assert.deepEqual(off.removeRuleIds, on.removeRuleIds);
  assert.equal((store.data["viewer-settings"] as { force: boolean }).force, false);
});

test("规则把整页导航改跳到展示页，地址里带上原来那个文件", () => {
  const [first] = rules("chrome-extension://viewer/");

  assert.equal(first?.action.redirect?.regexSubstitution, "chrome-extension://viewer/viewer.html?file=\\0");
  assert.deepEqual(first?.condition.resourceTypes, ["main_frame"]);
});

test("只拦实测会被下载的那几类，别的一概不碰", () => {
  const matches = (url: string) =>
    rules("chrome-extension://viewer/").some((rule) =>
      new RegExp(rule.condition.regexFilter ?? "").test(url),
    );

  assert.equal(matches("file:///tmp/a.yaml"), true);
  assert.equal(matches("file:///tmp/a.yml"), true);
  assert.equal(matches("file:///tmp/a.csv"), true);
  // markdown、源码这些浏览器本来就会内联显示，拦了反而是改掉用户熟悉的行为。
  assert.equal(matches("file:///tmp/a.md"), false);
  assert.equal(matches("file:///tmp/a.go"), false);
  assert.equal(matches("https://example.test/a.yaml"), false);
});

test("展示页按真实文件地址渲染，和直接打开那个文件一样", async () => {
  setup();
  const dom = page("", { url: "chrome-extension://viewer/viewer.html?file=x" });
  const doc = dom.window.document;
  globalThis.fetch = (async () => ({ text: async () => "a: 1\n" })) as unknown as typeof fetch;

  await load(doc, "file:///tmp/conf.yaml");

  // 页面地址是扩展自己的地址，分流看的是这个。
  assert.equal(doc.documentElement.dataset["lfvUrl"], "file:///tmp/conf.yaml");
  assert.equal(doc.title, "conf.yaml");
  for (let i = 0; i < 200 && !doc.querySelector(".lfv-data.lfv-rendered"); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  // yaml 走的是折叠树那条路，和直接打开 `file:///tmp/conf.yaml` 完全一样。
  assert.ok(doc.querySelector(".lfv-data.lfv-rendered"));
  assert.equal(doc.querySelector(".lfv-key")?.textContent, "a: ");
  assert.ok(doc.documentElement.classList.contains("lfv-on"));
});

test("展示页从地址里取出要显示的文件", () => {
  assert.equal(
    fileOf(`chrome-extension://viewer/viewer.html?file=${encodeURIComponent("file:///tmp/a b.csv")}`),
    "file:///tmp/a b.csv",
  );
  assert.equal(fileOf("chrome-extension://viewer/viewer.html"), "");
});

test("Firefox 上没有那个开关，改说一句话，也不弹欢迎页", async () => {
  setup();
  // Firefox 的 chrome.extension 没有 isAllowedFileSchemeAccess 这个方法。
  (globalThis as unknown as { chrome: { extension: Record<string, unknown> } }).chrome.extension = {};

  const doc = settingsPage().window.document;
  await renderSettings(doc);

  assert.equal((doc.getElementById("lfv-steps") as HTMLElement).hidden, true);
  assert.equal((doc.getElementById("lfv-firefox") as HTMLElement).hidden, false);
  assert.equal((doc.getElementById("lfv-access") as HTMLElement).dataset["state"], "on");
  assert.equal(await welcome("install"), false);
  assert.deepEqual(opened, []);
});

test("展示页要跳本地文件时，后台脚本替它跳", () => {
  const updated: { id: number; url: string }[] = [];
  installChrome({
    tabs: { update: async (id: number, { url }: { url: string }) => void updated.push({ id, url }) },
  });

  assert.equal(openLocal({ type: "lfv-open", url: "file:///tmp/a.md" }, 7), true);
  assert.deepEqual(updated, [{ id: 7, url: "file:///tmp/a.md" }]);
});

test("不是本地文件、不是这条消息、没有标签页，后台一律不动", () => {
  installChrome({
    tabs: {
      update: async () => {
        throw new Error("这几种情况都不该去跳转");
      },
    },
  });

  // 网页地址不跳：扩展页面自己就能导航到 http(s)，走这条路等于给了页面一个乱跳的口子。
  assert.equal(openLocal({ type: "lfv-open", url: "https://example.test/" }, 7), false);
  assert.equal(openLocal({ type: "别的消息", url: "file:///tmp/a.md" }, 7), false);
  assert.equal(openLocal({ type: "lfv-open", url: "file:///tmp/a.md" }, undefined), false);
  assert.equal(openLocal(null, 7), false);
});

test("主题卡片列出四种介质加自定义，点一下就存下来", async () => {
  const store = setup();
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const cards = [...doc.querySelectorAll("#lfv-themes .lfv-theme")] as HTMLElement[];
  assert.deepEqual(
    cards.map((card) => card.dataset["theme"]),
    ["paper", "night", "eink", "moss", "custom"],
  );
  // 默认那一张是按下去的状态。
  assert.equal(cards[1]?.getAttribute("aria-pressed"), "true");
  // 小样按各自主题的颜色画，不是清一色。
  const chips = cards.map((card) => card.querySelector(".lfv-chip")?.getAttribute("style") ?? "");
  assert.equal(new Set(chips.slice(0, 4)).size, 4);

  cards[0]?.click();
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal((store.data["viewer-settings"] as { theme: string }).theme, "paper");
});

test("选中的主题把颜色和排版写到页面上", async () => {
  setup({ stored: { "viewer-settings": { theme: "paper" } } });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const style = doc.documentElement.style;
  assert.equal(style.getPropertyValue("--background"), "#faf8f4");
  assert.equal(style.getPropertyValue("--lfv-size"), "16.5px");
  assert.equal(style.colorScheme, "light");
  assert.equal(doc.documentElement.dataset["lfvTheme"], "paper");
});

test("自定义那一块只在选了自定义时出现，改一个颜色只存改过的那个", async () => {
  const store = setup({ stored: { "viewer-settings": { theme: "custom" } } });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const box = doc.getElementById("lfv-custom") as HTMLElement;
  assert.equal(box.hidden, false);

  const inputs = [...doc.querySelectorAll("#lfv-colors input")] as HTMLInputElement[];
  assert.equal(inputs.length, 8);
  // 默认值来自底子那套（夜的页面底色）。
  assert.equal(inputs[0]?.value, "#16171a");

  inputs[0]!.value = "#101010";
  inputs[0]!.dispatchEvent(new (doc.defaultView as unknown as { Event: typeof Event }).Event("input"));
  await new Promise((resolve) => setTimeout(resolve, 10));

  // 只存改过的那一个，其余跟着底子走——底子以后调了色，自定义不会停在旧值上。
  const saved = store.data["viewer-settings"] as { custom: { base: string; patch: object } };
  assert.deepEqual(saved.custom, { base: "night", patch: { background: "#101010" } });
  assert.equal(doc.documentElement.style.getPropertyValue("--background"), "#101010");
});

test("排版滑块把字号写成带单位的值", async () => {
  const store = setup({ stored: { "viewer-settings": { theme: "custom" } } });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  const sliders = [...doc.querySelectorAll("#lfv-type input")] as HTMLInputElement[];
  assert.deepEqual(sliders.map((s) => s.type), ["range", "range", "range"]);
  assert.equal(sliders[0]?.value, "15.5");

  sliders[0]!.value = "19";
  sliders[0]!.dispatchEvent(new (doc.defaultView as unknown as { Event: typeof Event }).Event("input"));
  await new Promise((resolve) => setTimeout(resolve, 10));

  const saved = store.data["viewer-settings"] as { custom: { patch: Record<string, string> } };
  assert.equal(saved.custom.patch["lfv-size"], "19px");
});

test("恢复按钮把改动全清掉，底子留着", async () => {
  const store = setup({
    stored: {
      "viewer-settings": {
        theme: "custom",
        custom: { base: "paper", patch: { background: "#000000" } },
      },
    },
  });
  const doc = settingsPage().window.document;
  await renderSettings(doc);

  (doc.getElementById("lfv-reset") as HTMLElement).click();
  await new Promise((resolve) => setTimeout(resolve, 10));

  const saved = store.data["viewer-settings"] as { custom: { base: string; patch: object } };
  assert.deepEqual(saved.custom, { base: "paper", patch: {} });
});

test("设置页的预览块不叫 lfv-preview——那个类名归目录列表的悬停预览", () => {
  const html = readFileSync(new URL("../src/settings.html", import.meta.url), "utf8");

  // `.lfv-preview` 带 max-height + overflow:hidden（目录列表里悬停浮出的那几行），
  // 设置页的效果预览撞上它会被裁掉一半——2026-09-18 截图时抓到过一次。
  assert.equal(html.includes("lfv-preview"), false);
  assert.equal(html.includes('class="lfv-sample lfv-md"'), true);
});
