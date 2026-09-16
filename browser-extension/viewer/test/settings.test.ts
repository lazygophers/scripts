import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { clearChrome, installChrome, page, storageMock } from "./mock.ts";
import { fileAccess, readSettings, renderSettings, writeSettings } from "../src/settings.ts";
import { fileOf, load } from "../src/page.ts";

/** 本轮开的标签页。欢迎页和展示页都是这么开出来的。 */
let opened: string[] = [];

/** 本轮被取消、被抹掉的下载。 */
let cancelled: number[] = [];
let erased: number[] = [];

/**
 * 装一套假的 chrome。`allowed` 是浏览器里「允许访问文件网址」那个开关的状态。
 */
function setup({ allowed = true, stored = {} as Record<string, unknown> } = {}) {
  opened = [];
  cancelled = [];
  erased = [];
  installChrome({
    runtime: {
      getURL: (path: string) =>
        // 懒加载的包在浏览器里是 dist 产物，测试里指回源码，`import()` 才真的加载得动。
        path === "data.js"
          ? new URL("../src/data.ts", import.meta.url).href
          : `chrome-extension://viewer/${path}`,
      onInstalled: { addListener: () => {} },
    },
    extension: { isAllowedFileSchemeAccess: async () => allowed },
    tabs: {
      create: async ({ url }: { url: string }) => {
        opened.push(url);
      },
    },
    downloads: {
      onCreated: { addListener: () => {} },
      cancel: async (id: number) => {
        cancelled.push(id);
      },
      erase: async ({ id }: { id: number }) => {
        erased.push(id);
      },
    },
  });
  return storageMock(stored);
}

// `background.ts` 一加载就去挂浏览器的事件监听，所以先把假 chrome 装上再动态加载它。
setup();
const { interceptDownload, welcome } = await import("../src/background.ts");

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
     <label><input id="lfv-force" type="checkbox" /></label>`,
    { url },
  );
}

test("设置默认是关的，改了之后存得住", async () => {
  const store = setup();

  assert.deepEqual(await readSettings(), { force: false });

  await writeSettings({ force: true });

  // 存的是扩展自己的存储，浏览器重启照样在——这里用「换一个新进程再读一次」模拟。
  assert.deepEqual(await readSettings(), { force: true });
  assert.deepEqual(store.data["viewer-settings"], { force: true });
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

  assert.deepEqual(store.data["viewer-settings"], { force: false });
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

test("开关关着时下载照旧，一个字节都不拦", async () => {
  setup();

  assert.equal(await interceptDownload({ id: 7, url: "file:///tmp/a.yaml" }), false);
  assert.deepEqual(cancelled, []);
  assert.deepEqual(opened, []);
});

test("开关打开后会被下载的本地文本文件改在展示页里打开", async () => {
  setup({ stored: { "viewer-settings": { force: true } } });

  assert.equal(await interceptDownload({ id: 7, url: "file:///tmp/a b.yaml" }), true);
  assert.deepEqual(cancelled, [7]);
  // 取消掉的那条也从下载列表里抹掉。
  assert.deepEqual(erased, [7]);
  assert.deepEqual(opened, [
    `chrome-extension://viewer/viewer.html?file=${encodeURIComponent("file:///tmp/a b.yaml")}`,
  ]);
});

test("开关打开也只管本地的、viewer 认得的那些文件", async () => {
  setup({ stored: { "viewer-settings": { force: true } } });

  // 网上下来的文件不碰。
  assert.equal(await interceptDownload({ id: 1, url: "https://example.test/a.yaml" }), false);
  // 本地的压缩包 viewer 本来就不管。
  assert.equal(await interceptDownload({ id: 2, url: "file:///tmp/a.zip" }), false);
  assert.deepEqual(cancelled, []);
});

test("重定向过的下载按最终地址判断", async () => {
  setup({ stored: { "viewer-settings": { force: true } } });

  const taken = await interceptDownload({
    id: 3,
    url: "https://example.test/x",
    finalUrl: "file:///tmp/a.csv",
  });

  assert.equal(taken, true);
  assert.deepEqual(cancelled, [3]);
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
