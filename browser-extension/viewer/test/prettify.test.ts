import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { classify, isPlainTextPage, prettify } from "../src/prettify.ts";
import { clearChrome, installChrome, page } from "./mock.ts";

beforeEach(() => {
  installChrome({ runtime: { getURL: (path: string) => `chrome-extension://viewer/${path}` } });
});

afterEach(clearChrome);

/** 造一张浏览器打开本地文本文件时生成的页面：正文只有一个 `<pre>`，没有标题。 */
function textFile(url: string, text: string, contentType = "text/plain") {
  return page(`<pre>${text}</pre>`, { url, contentType });
}

const toggle = (doc: Document) => doc.querySelector(".lfv-toggle") as HTMLElement;
const first = (doc: Document) => doc.body.children[0] as HTMLElement;

test("白名单类型的纯文本页直接接管", () => {
  const dom = textFile("file:///tmp/notes.md", "# 标题");
  prettify(dom.window.document);

  const doc = dom.window.document;
  assert.equal(doc.querySelector(".lfv-text")?.textContent, "# 标题");
  assert.ok(doc.documentElement.classList.contains("lfv-on"));
  assert.equal(
    doc.querySelector("link#lfv-style")?.getAttribute("href"),
    "chrome-extension://viewer/viewer.css",
  );
});

test("切回原始拿到的是浏览器原来那个节点，逐字节相同", () => {
  const dom = textFile("file:///tmp/main.go", "package main");
  const doc = dom.window.document;
  const original = first(doc);
  const before = original.outerHTML;

  prettify(doc);
  toggle(doc).click();

  assert.equal(first(doc), original);
  assert.equal(first(doc).outerHTML, before);
  assert.equal(doc.documentElement.classList.contains("lfv-on"), false);

  // 再点一次回到美化，来回都成立。
  toggle(doc).click();
  assert.equal(doc.querySelector(".lfv-text")?.textContent, "package main");
  assert.ok(doc.documentElement.classList.contains("lfv-on"));
});

test("普通网页一个字节都不碰", () => {
  const dom = page("<h1>Hello</h1><p>world</p>", { url: "https://example.test/a" });
  const doc = dom.window.document;
  doc.title = "Example";
  const before = doc.body.innerHTML;

  prettify(doc);

  assert.equal(doc.body.innerHTML, before);
  assert.equal(doc.querySelector(".lfv-toggle"), null);
});

test("扩展名是网页的本地文件也不碰", () => {
  const dom = textFile("file:///tmp/page.html", "<b>x</b>", "text/html");
  const doc = dom.window.document;
  const before = doc.body.innerHTML;

  prettify(doc);

  assert.equal(doc.body.innerHTML, before);
  assert.equal(doc.querySelector(".lfv-toggle"), null);
});

test("拿不准的纯文本页只多一个「美化一下」按钮，点了才渲染", () => {
  const dom = textFile("file:///tmp/data.unknownext", "hello");
  const doc = dom.window.document;

  prettify(doc);

  assert.equal(toggle(doc).textContent, "美化一下");
  assert.equal(first(doc).tagName, "PRE");
  assert.equal(doc.querySelector("link#lfv-style"), null);
  assert.equal(doc.documentElement.classList.contains("lfv-on"), false);

  toggle(doc).click();

  assert.equal(doc.querySelector(".lfv-text")?.textContent, "hello");
  assert.equal(doc.querySelectorAll(".lfv-toggle").length, 1);
});

test("超过 2MB 只出原始文本加一个「强制美化」按钮", () => {
  const big = "x".repeat(2 * 1024 * 1024 + 1);
  const dom = textFile("file:///tmp/huge.log", big);
  const doc = dom.window.document;

  prettify(doc);

  assert.equal(toggle(doc).textContent, "强制美化");
  assert.equal(doc.querySelector(".lfv-text"), null);

  toggle(doc).click();

  assert.equal(doc.querySelector(".lfv-text")?.textContent?.length, big.length);
});

test("原始 / 美化的选择不跨页记忆", () => {
  const first = textFile("file:///tmp/a.py", "print(1)");
  prettify(first.window.document);
  toggle(first.window.document).click(); // 这一页切到原始

  const second = textFile("file:///tmp/b.py", "print(2)");
  prettify(second.window.document);

  assert.equal(second.window.document.querySelector(".lfv-text")?.textContent, "print(2)");
  assert.ok(second.window.document.documentElement.classList.contains("lfv-on"));
});

test("三条判定缺一不可", () => {
  const ok = textFile("file:///tmp/a.txt", "hi");
  assert.equal(isPlainTextPage(ok.window.document), true);

  const wrongType = textFile("file:///tmp/a.txt", "hi", "image/png");
  assert.equal(isPlainTextPage(wrongType.window.document), false);

  const titled = textFile("file:///tmp/a.txt", "hi");
  titled.window.document.title = "x";
  assert.equal(isPlainTextPage(titled.window.document), false);

  const twoKids = page("<pre>hi</pre><div></div>", { contentType: "text/plain" });
  assert.equal(isPlainTextPage(twoKids.window.document), false);
});

test("类型分档按扩展名，带 query 和 hash 也认得出来", () => {
  assert.equal(classify("file:///tmp/a.md"), "whitelist");
  assert.equal(classify("file:///tmp/a.yaml?x=1#y"), "whitelist");
  assert.equal(classify("file:///tmp/a.svg"), "webpage");
  assert.equal(classify("file:///tmp/README"), "unknown");
  assert.equal(classify("file:///tmp/.bashrc"), "unknown");
});
