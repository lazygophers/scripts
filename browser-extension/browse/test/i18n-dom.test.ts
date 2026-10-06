/**
 * `localize()` / `msg()` 的 DOM 行为（i18n.test.ts 测的是语言文件本身）。
 * key 缺失或消息为空时节点必须保持原样，不能被填成 undefined。
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

import { localize, msg } from "../src/i18n.ts";

type Any = Record<string, unknown>;

const MESSAGES: Record<string, string> = {
  hello: "你好",
  fill: "搜: $1",
  withPlaceholder: "有提示",
};

function realm(markup: string): void {
  const dom = new JSDOM(`<!doctype html><body>${markup}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string, args?: string[]) => {
        const template = MESSAGES[key];
        if (template === undefined) {
          return "";
        }
        return args?.length
          ? template.replace(/\$(\d)/g, (_, i) => args[Number(i) - 1] ?? "")
          : template;
      },
    },
  };
}

afterEach(() => {
  const g = globalThis as Any;
  delete g.document;
  delete g.HTMLElement;
  delete g.chrome;
});

test("localize fills known keys and leaves unknown ones alone", () => {
  realm(
    `<p id="a" data-i18n="hello">原始</p>` +
      `<p id="b" data-i18n="missing">保留</p>` +
      `<p id="c">没有属性</p>`,
  );
  localize();
  assert.equal(document.getElementById("a")?.textContent, "你好");
  assert.equal(document.getElementById("b")?.textContent, "保留", "消息为空时不填");
  assert.equal(document.getElementById("c")?.textContent, "没有属性");
  assert.equal(document.documentElement.lang, "zh-CN");
});

test("localize fills input placeholders; an empty data-i18n is skipped", () => {
  realm(
    `<input id="i" data-i18n-placeholder="withPlaceholder">` +
      `<input id="j" data-i18n-placeholder="missing" placeholder="留着">` +
      `<p data-i18n="">空 key</p>`,
  );
  localize();
  const i = document.getElementById("i") as HTMLInputElement;
  const j = document.getElementById("j") as HTMLInputElement;
  assert.equal(i.placeholder, "有提示");
  assert.equal(j.placeholder, "留着", "消息为空时不动 placeholder");
});

test("localize accepts a root narrower than the document", () => {
  realm(`<div id="root"><p data-i18n="hello">x</p></div><p data-i18n="hello">y</p>`);
  const root = document.getElementById("root")!;
  localize(root);
  assert.equal(root.querySelector("p")?.textContent, "你好");
});

test("msg substitutes $1-style arguments", () => {
  realm("");
  assert.equal(msg("fill", "猫"), "搜: 猫");
  assert.equal(msg("missing"), "");
});

test("a bare data-i18n-placeholder attribute resolves to an empty message", () => {
  realm(`<input id="k" data-i18n-placeholder placeholder="keep">`);
  localize();
  const k = document.getElementById("k") as HTMLInputElement;
  assert.equal(k.placeholder, "keep", "空消息不动 placeholder");
});
