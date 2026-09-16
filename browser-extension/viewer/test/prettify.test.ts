import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { colorize } from "../src/highlight.ts";
import { classify, isPlainTextPage, prettify } from "../src/prettify.ts";
import { clearChrome, installChrome, page } from "./mock.ts";

/** 本轮 `getURL` 被问过的路径。用来证明非代码页从没去取过高亮包。 */
let asked: string[] = [];

beforeEach(() => {
  asked = [];
  installChrome({
    runtime: {
      getURL: (path: string) => {
        asked.push(path);
        // 高亮包在浏览器里是 dist 产物，测试里直接指回源码，让 `import()` 真的能加载。
        if (path === "highlight.js") return new URL("../src/highlight.ts", import.meta.url).href;
        return `chrome-extension://viewer/${path}`;
      },
    },
  });
});

afterEach(clearChrome);

/** 等着色那一拍落地。`lfv-colored` 是着色结束的信号，不着色也会打上。 */
async function colored(doc: Document): Promise<HTMLElement> {
  for (let i = 0; i < 200; i += 1) {
    const code = doc.querySelector("code.lfv-colored");
    if (code) return code as HTMLElement;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到着色完成");
}

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

test("常见语言的源码文件着上色，每行左边有行号", async () => {
  const dom = textFile("file:///tmp/main.go", 'package main\n\nfunc main() {\n\tprintln("hi")\n}\n');
  const doc = dom.window.document;

  prettify(doc);

  // 先出纯文本，再补色：挂上去那一刻就已经能读。
  assert.equal(doc.querySelector(".lfv-code .lfv-text")?.textContent?.startsWith("package main"), true);
  assert.equal(doc.querySelector(".lfv-gutter")?.textContent, "1\n2\n3\n4\n5");

  const code = await colored(doc);
  assert.ok(code.querySelector(".hljs-keyword"), "关键字应该被包成 token");
  assert.equal(code.textContent, 'package main\n\nfunc main() {\n\tprintln("hi")\n}\n');
});

test("复制按钮把全文放进剪贴板，不含行号，并给出反馈", async () => {
  const dom = textFile("file:///tmp/a.py", "print(1)\nprint(2)\n");
  const doc = dom.window.document;
  let copied: string | null = null;
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: (text: string) => {
        copied = text;
        return Promise.resolve();
      },
    },
  });

  prettify(doc);
  const button = doc.querySelector(".lfv-copy") as HTMLElement;
  button.click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(copied, "print(1)\nprint(2)\n");
  assert.equal(button.textContent, "已复制");
});

test("扩展名不是源码时不走代码视图，仍是纯文本", () => {
  const dom = textFile("file:///tmp/notes.log", "line one\nline two\n");
  const doc = dom.window.document;

  prettify(doc);

  assert.equal(doc.querySelector(".lfv-code"), null);
  assert.equal(doc.querySelector(".lfv-text")?.textContent, "line one\nline two\n");
});

test("冷门语言不在合集里就返回 null，由调用方保留纯文本", () => {
  assert.equal(colorize("BEGIN { print 1 }", "brainfuck"), null);
  assert.ok(colorize("print(1)", "python")?.includes("hljs-"));
});

test("空文件不报错，显示为空内容", async () => {
  const dom = textFile("file:///tmp/empty.go", "");
  const doc = dom.window.document;

  prettify(doc);
  const code = await colored(doc);

  assert.equal(code.textContent, "");
  assert.equal(doc.querySelector(".lfv-gutter")?.textContent, "");
});

test("非代码页面不去加载高亮库", () => {
  const dom = textFile("file:///tmp/notes.md", "# 标题");

  prettify(dom.window.document);

  assert.equal(asked.includes("highlight.js"), false);
  assert.equal(dom.window.document.querySelector(".lfv-code"), null);
});

test("类型分档按扩展名，带 query 和 hash 也认得出来", () => {
  assert.equal(classify("file:///tmp/a.md"), "whitelist");
  assert.equal(classify("file:///tmp/a.yaml?x=1#y"), "whitelist");
  assert.equal(classify("file:///tmp/a.svg"), "webpage");
  assert.equal(classify("file:///tmp/README"), "unknown");
  assert.equal(classify("file:///tmp/.bashrc"), "unknown");
});
