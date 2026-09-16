import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { collectPageSnapshot, markdownFromSnapshot, type PageSnapshot } from "../src/export-markdown.ts";

function parser(): DOMParser {
  const dom = new JSDOM();
  return new dom.window.DOMParser() as unknown as DOMParser;
}

function snapshot(overrides: Partial<PageSnapshot> = {}): PageSnapshot {
  return {
    title: "Example page",
    url: "https://example.test/articles/post",
    selectionHtml: "",
    documentHtml: "<!doctype html><html><body><main><h1>Article</h1><p>Hello <strong>world</strong>.</p></main></body></html>",
    ...overrides,
  };
}

test("选区优先于网页正文并把相对链接改为绝对地址", () => {
  const markdown = markdownFromSnapshot(
    snapshot({ selectionHtml: '<p>Chosen <a href="/docs">documentation</a></p>' }),
    parser(),
  );

  assert.match(markdown, /^# Example page\n\n来源：<https:\/\/example\.test\/articles\/post>/u);
  assert.match(markdown, /Chosen \[documentation\]\(https:\/\/example\.test\/docs\)/u);
  assert.doesNotMatch(markdown, /Article/u);
});

test("无选区时由 Readability 提取主要正文", () => {
  const paragraphs = Array.from({ length: 6 }, (_, index) => `<p>Paragraph ${index}: substantial article content for extraction.</p>`).join("");
  const markdown = markdownFromSnapshot(
    snapshot({
      documentHtml: `<!doctype html><html><head><title>Readable</title></head><body><nav>Menu</nav><article><h1>Readable heading</h1>${paragraphs}</article></body></html>`,
    }),
    parser(),
  );

  assert.match(markdown, /Readable heading/u);
  assert.match(markdown, /Paragraph 5/u);
});

test("Readability 无法提取时使用 body", () => {
  const markdown = markdownFromSnapshot(
    snapshot({ title: "  ", documentHtml: "<html><body><div>Small fallback</div></body></html>" }),
    parser(),
  );
  assert.match(markdown, /^# 未命名网页/u);
  assert.match(markdown, /Small fallback/u);
});

test("空网页明确报错", () => {
  assert.throws(
    () => markdownFromSnapshot(snapshot({ documentHtml: "<html><body></body></html>" }), parser()),
    /没有可导出的文字内容/u,
  );
});

test("代码块保持围栏格式且不做 Markdown 转义", () => {
  const codeBlock = [
    '<pre><code class="language-js">const a = 1 * 2;',
    'if (a &gt; 1) {',
    '  console.log("hi");',
    "}</code></pre>",
  ].join("\n");
  const markdown = markdownFromSnapshot(
    snapshot({ selectionHtml: `${codeBlock}<p>after</p>` }),
    parser(),
  );

  assert.match(markdown, /```js\nconst a = 1 \* 2;\nif \(a > 1\) \{\n  console\.log\("hi"\);\n\}\n```/u);
  assert.doesNotMatch(markdown, /\\\*|&gt;|&lt;/u);
});

test("无语言代码块仍用围栏包裹", () => {
  const markdown = markdownFromSnapshot(
    snapshot({ selectionHtml: "<pre><code>plain *text*\nline two</code></pre>" }),
    parser(),
  );
  assert.match(markdown, /```\nplain \*text\*\nline two\n```/u);
});

test("关闭标题和来源开关后只导出正文", () => {
  const markdown = markdownFromSnapshot(
    snapshot({ selectionHtml: "<p>Only body</p>" }),
    parser(),
    { includeTitle: false, includeSource: false },
  );
  assert.equal(markdown, "Only body\n");
});

test("页面快照读取选区 HTML", () => {
  const dom = new JSDOM("<html><head><title>Selection</title></head><body><p>Hello <strong>selected</strong> text</p></body></html>", {
    url: "https://example.test/page",
  });
  const range = dom.window.document.createRange();
  range.selectNode(dom.window.document.querySelector("strong")!);
  const selection = dom.window.getSelection();
  selection!.removeAllRanges();
  selection!.addRange(range);

  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  try {
    const result = collectPageSnapshot();
    assert.equal(result.selectionHtml, "<strong>selected</strong>");
    assert.equal(result.title, "Selection");
    assert.equal(result.url, "https://example.test/page");
  } finally {
    Object.assign(globalThis, { window: previousWindow, document: previousDocument });
  }
});
