/**
 * 确认弹窗页的缺省自举：占位符只剩 #url，按钮全没有，参数也一个不给。
 * 量的是：缺参回退到问号、空 url 回退到整浏览器措辞、缺按钮不绑监听也不炸。
 * confirm-page.test.ts 共用一份完整页面，缺席分支需要独立文件的一次独立导入
 * （同 URL 再 import 是缓存命中，模块不会重跑）。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

type Any = Record<string, unknown>;
const g = globalThis as Any;

afterEach(() => {
  delete g.document;
  delete g.location;
  delete g.chrome;
});

test("缺按钮缺参数的页面上自举：占位符回退措辞，不炸、不回话", async () => {
  const dom = new JSDOM(
    "<!doctype html><body><span id=\"url\"></span><p>nothing here</p></body>",
    { url: "https://ext.test/confirm.html" },
  );
  const sent: Any[] = [];
  g.document = dom.window.document;
  g.location = dom.window.location;
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string) => `[${key}]`,
    },
    runtime: {
      sendMessage: async (message: Any) => {
        sent.push(message);
      },
    },
  };

  await import("../src/confirm-page.ts");
  assert.equal(
    dom.window.document.getElementById("url")!.textContent,
    "[confirmWholeBrowser]",
    "没有 url 参数就回退到「整个浏览器」的措辞",
  );
  assert.deepEqual(sent, [], "没有按钮就没有回话");
});
