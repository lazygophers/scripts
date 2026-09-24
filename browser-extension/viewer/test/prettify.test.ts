import assert from "node:assert/strict";
import type { JSDOM } from "jsdom";
import { afterEach, beforeEach, test } from "node:test";

import { colorize } from "../src/highlight.ts";
import { LAZY, classify, isDirectoryIndex, isPlainTextPage, prettify, show } from "../src/prettify.ts";
import { clearChrome, installChrome, page, storageMock } from "./mock.ts";

/** 本轮 `getURL` 被问过的路径。用来证明非代码页从没去取过高亮包。 */
let asked: string[] = [];

/** 本轮发给后台脚本的消息。展示页里点本地链接时应该有，文件页里一条都不该有。 */
let sent: unknown[] = [];

beforeEach(() => {
  asked = [];
  sent = [];
  installChrome({
    runtime: {
      sendMessage: async (message: unknown) => {
        sent.push(message);
      },
      getURL: (path: string) => {
        asked.push(path);
        // 懒加载的两个包在浏览器里是 dist 产物，测试里直接指回源码，让 `import()` 真的能加载。
        if (path === "highlight.js") return new URL("../src/highlight.ts", import.meta.url).href;
        if (path === "markdown.js") return new URL("../src/markdown.ts", import.meta.url).href;
        // 画图和公式那两个包在 node 里跑不起来（一个要真的排版，一个 import 了 CSS），换成桩。
        if (path === "mermaid.js") return new URL("./stub-mermaid.ts", import.meta.url).href;
        if (path === "katex.js") return new URL("./stub-katex.ts", import.meta.url).href;
        if (path === "data.js") return new URL("../src/data.ts", import.meta.url).href;
        if (path === "csv.js") return new URL("../src/csv.ts", import.meta.url).href;
        if (path === "log.js") return new URL("../src/log.ts", import.meta.url).href;
        if (path === "listing.js") return new URL("../src/listing.ts", import.meta.url).href;
        if (path === "search.js") return new URL("../src/search.ts", import.meta.url).href;
        return `chrome-extension://viewer/${path}`;
      },
    },
  });
  // 主题从 `chrome.storage.local` 读，所以每个用例都要有这份存储桩。
  storageMock();
});

/** 链接那几个用例会把 `fetch` 换成桩，跑完放回来。 */
const realFetch = globalThis.fetch;

afterEach(() => {
  clearChrome();
  globalThis.fetch = realFetch;
});

/** 等着色那一拍落地。`lfv-colored` 是着色结束的信号，不着色也会打上。 */
async function colored(doc: Document): Promise<HTMLElement> {
  for (let i = 0; i < 200; i += 1) {
    const code = doc.querySelector("code.lfv-colored");
    if (code) return code as HTMLElement;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到着色完成");
}

/** 等 markdown 那一拍落地。`lfv-rendered` 是排版加着色都结束的信号。 */
async function rendered(doc: Document): Promise<HTMLElement> {
  for (let i = 0; i < 200; i += 1) {
    const host = doc.querySelector(".lfv-doc.lfv-rendered");
    if (host) return host as HTMLElement;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到 markdown 渲染完成");
}

/** 造一张浏览器打开本地文本文件时生成的页面：正文只有一个 `<pre>`，没有标题。 */
function textFile(url: string, text: string, contentType = "text/plain") {
  return page(`<pre>${text}</pre>`, { url, contentType });
}

const toggle = (doc: Document) => doc.querySelector(".lfv-toggle") as HTMLElement;
const first = (doc: Document) => doc.body.children[0] as HTMLElement;

test("白名单类型的纯文本页直接接管", () => {
  const dom = textFile("file:///tmp/notes.txt", "# 标题");
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
  const dom = textFile("file:///tmp/huge.conf", big);
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
  const dom = textFile("file:///tmp/notes.conf", "line one\nline two\n");
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

test("没有围栏代码块的 markdown 不去加载高亮库", async () => {
  const dom = textFile("file:///tmp/notes.md", "# 标题");
  const doc = dom.window.document;

  prettify(doc);
  await rendered(doc);

  assert.equal(asked.includes("markdown.js"), true);
  assert.equal(asked.includes("highlight.js"), false);
  assert.equal(doc.querySelector(".lfv-code"), null);
});

test("常见 markdown 语法都排好版", async () => {
  const source = [
    "# 大标题",
    "",
    "一段正文，带 `行内代码` 和 [链接](https://example.test/)。",
    "",
    "- 无序一",
    "- 无序二",
    "",
    "1. 有序一",
    "",
    "> 引用",
    "",
    "| 头 | 值 |",
    "| --- | --- |",
    "| a | 1 |",
    "",
    "---",
    "",
    "![图](p.png)",
    "",
  ].join("\n");
  const dom = textFile("file:///tmp/readme.md", source);
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  assert.equal(host.querySelector("h1")?.textContent?.startsWith("大标题"), true);
  assert.equal(host.querySelector("p code")?.textContent, "行内代码");
  assert.equal(host.querySelector("p a")?.getAttribute("href"), "https://example.test/");
  assert.equal(host.querySelectorAll("ul li").length, 2);
  assert.equal(host.querySelectorAll("ol li").length, 1);
  assert.equal(host.querySelector("blockquote")?.textContent?.trim(), "引用");
  assert.equal(host.querySelector("table td")?.textContent, "a");
  assert.ok(host.querySelector("hr"));
  assert.equal(host.querySelector("img")?.getAttribute("src"), "p.png");
});

test("围栏代码块按标注的语言着色", async () => {
  const dom = textFile("file:///tmp/readme.md", "```go\npackage main\n```\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const code = host.querySelector("pre > code.language-go") as HTMLElement;
  assert.ok(code.querySelector(".hljs-keyword"), "关键字应该被包成 token");
  assert.equal(code.textContent?.trim(), "package main");
  assert.equal(asked.includes("highlight.js"), true);
});

test("front matter 变成顶部信息表，不出现在正文里", async () => {
  const dom = textFile("file:///tmp/post.md", "---\ntitle: 一篇文章\ntags: a, b\n---\n\n正文\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const rows = host.querySelectorAll(".lfv-front-matter tr");
  assert.equal(rows.length, 2);
  assert.equal(rows[0]?.querySelector("th")?.textContent, "title");
  assert.equal(rows[0]?.querySelector("td")?.textContent, "一篇文章");
  assert.equal(host.querySelector("p")?.textContent, "正文");
  assert.equal(host.textContent?.includes("---"), false);
});

test("没有 front matter 就没有信息表", async () => {
  const dom = textFile("file:///tmp/plain.md", "正文\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  assert.equal(host.querySelector(".lfv-front-matter"), null);
});

test("标题带锚点，同名标题各有各的地址", async () => {
  const dom = textFile("file:///tmp/a.md", "## 安装\n\n## 安装\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const heads = host.querySelectorAll("h2");
  assert.equal(heads[0]?.getAttribute("id"), "安装");
  assert.equal(heads[1]?.getAttribute("id"), "安装-1");
  assert.equal(heads[0]?.querySelector("a.lfv-anchor")?.getAttribute("href"), "#安装");
  assert.equal(heads[1]?.querySelector("a.lfv-anchor")?.getAttribute("href"), "#安装-1");
});

test("带锚点的地址打开时滚到那个标题", async () => {
  const dom = textFile("file:///tmp/a.md#%E5%AE%89%E8%A3%85", "# 开头\n\n## 安装\n");
  const doc = dom.window.document;
  let scrolled: Element | null = null;
  // jsdom 没实现 scrollIntoView，补一个只记录被滚到谁身上的桩。
  Object.defineProperty(dom.window.Element.prototype, "scrollIntoView", {
    configurable: true,
    value: function scrollIntoView(this: Element) {
      scrolled = this;
    },
  });

  prettify(doc);
  await rendered(doc);

  assert.equal((scrolled as Element | null)?.getAttribute("id"), "安装");
});

test("文档里的原始 HTML 过一遍净化，脚本被摘掉", async () => {
  const dom = textFile("file:///tmp/x.md", "");
  const doc = dom.window.document;
  // 直接写 textContent，免得这段 HTML 在造页面时就被 jsdom 当标签解析掉。
  first(doc).textContent =
    '<div id="keep">留着<script>window.pwned = 1</script><img src="x" onerror="window.pwned = 1"></div>\n';

  prettify(doc);
  const host = await rendered(doc);

  assert.equal(host.querySelector("#keep")?.textContent, "留着");
  assert.equal(host.querySelector("script"), null);
  assert.equal(host.querySelector("img")?.hasAttribute("onerror"), false);
  assert.equal((dom.window as unknown as { pwned?: number }).pwned, undefined);
});

test("markdown 左侧出现目录，按层级缩进，点条目跳到那一节", async () => {
  const dom = textFile("file:///tmp/long.md", "# 开头\n\n## 安装\n\n### 细节\n\n## 用法\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  // 目录排在正文前面，才是「左侧那一条」。
  assert.equal(host.children[0]?.className, "lfv-toc");
  const links = host.querySelectorAll(".lfv-toc a");
  assert.deepEqual(
    Array.from(links, (a) => [a.textContent, a.getAttribute("href"), (a as HTMLElement).dataset["level"]]),
    [
      ["开头", "#开头", "1"],
      ["安装", "#安装", "2"],
      ["细节", "#细节", "3"],
      ["用法", "#用法", "2"],
    ],
  );
  assert.ok(host.querySelector("#安装"), "目录指向的 id 在正文里真的存在");
});

test("滚动时目录高亮当前所在的那一节", async () => {
  const dom = textFile("file:///tmp/long.md", "## 一\n\n## 二\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  // jsdom 没有排版，自己摆两个标题的位置：一在视口上方，二还在下面。
  const place = (id: string, top: number) =>
    Object.defineProperty(doc.getElementById(id) as HTMLElement, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top }) as DOMRect,
    });
  place("一", -10);
  place("二", 500);
  dom.window.dispatchEvent(new dom.window.Event("scroll"));

  const links = host.querySelectorAll(".lfv-toc a");
  assert.equal(links[0]?.classList.contains("lfv-active"), true);
  assert.equal(links[1]?.classList.contains("lfv-active"), false);

  // 往下滚过第二个标题，高亮跟着走。
  place("二", 20);
  dom.window.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(links[0]?.classList.contains("lfv-active"), false);
  assert.equal(links[1]?.classList.contains("lfv-active"), true);
});

test("没有标题的文档不出目录，非 markdown 页面也没有", async () => {
  const dom = textFile("file:///tmp/flat.md", "只有一段话。\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  assert.equal(host.querySelector(".lfv-toc"), null);
  assert.equal(host.children.length, 1);

  const code = textFile("file:///tmp/main.go", "package main\n");
  prettify(code.window.document);
  assert.equal(code.window.document.querySelector(".lfv-toc"), null);
});

test("窄窗口下目录收起，宽窗口摊开", async () => {
  const narrow = textFile("file:///tmp/a.md", "## 一\n");
  Object.defineProperty(narrow.window, "innerWidth", { configurable: true, value: 600 });
  prettify(narrow.window.document);
  const narrowHost = await rendered(narrow.window.document);
  const narrowToc = narrowHost.querySelector(".lfv-toc") as HTMLDetailsElement;
  assert.equal(narrowToc.tagName, "DETAILS");
  assert.equal(narrowToc.open, false);
  assert.equal(narrowToc.querySelector("summary")?.textContent, "目录");

  const wide = textFile("file:///tmp/b.md", "## 一\n");
  Object.defineProperty(wide.window, "innerWidth", { configurable: true, value: 1440 });
  prettify(wide.window.document);
  const wideHost = await rendered(wide.window.document);
  assert.equal((wideHost.querySelector(".lfv-toc") as HTMLDetailsElement).open, true);
});

test("Obsidian 双方括号链接变成可点的链接", async () => {
  const dom = textFile("file:///tmp/a.md", "见 [[隔壁页面]] 和 [[api/index.md|接口文档]]。\n\n写坏的 [[没闭合。\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const links = host.querySelectorAll("a.lfv-wikilink");
  assert.equal(links.length, 2);
  assert.equal(links[0]?.getAttribute("href"), encodeURI("隔壁页面.md"));
  assert.equal(links[0]?.textContent, "隔壁页面");
  // 已经带扩展名的目标不再补 `.md`，还能自己写显示文字。
  assert.equal(links[1]?.getAttribute("href"), "api/index.md");
  assert.equal(links[1]?.textContent, "接口文档");
  // 没闭合的那段退回普通文本，整篇文档照常渲染。
  assert.equal(host.textContent?.includes("[[没闭合。"), true);
});

test("脚注渲染成上标加文末条目，两头互相跳", async () => {
  const dom = textFile("file:///tmp/a.md", "正文[^1]。\n\n[^1]: 一条注解。\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const ref = host.querySelector("sup a") as HTMLAnchorElement;
  const backref = host.querySelector(".footnotes a[href^='#']:last-of-type") as HTMLAnchorElement;
  assert.ok(ref, "正文里应该有上标编号");
  assert.equal(ref.getAttribute("href")?.startsWith("#"), true);
  // 上标指向的条目真的在文末脚注区里。
  const id = ref.getAttribute("href")?.slice(1) ?? "";
  assert.ok(host.querySelector(".footnotes")?.querySelector(`#${id}`), "脚注条目应该在文末");
  assert.equal(backref.getAttribute("href")?.startsWith("#"), true);
  assert.equal(host.querySelector(".footnotes")?.textContent?.includes("一条注解。"), true);
});

test("三冒号提示框按类型上色，认不得的类型按 note 处理", async () => {
  const dom = textFile(
    "file:///tmp/a.md",
    ":::warning\n小心**这里**\n:::\n\n:::外星类型\n还是块提示\n:::\n\n:::没闭合\n",
  );
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  const notes = host.querySelectorAll(".lfv-note");
  assert.equal(notes.length, 2);
  assert.equal(notes[0]?.className, "lfv-note lfv-note-warning");
  // 框里的 markdown 照常渲染。
  assert.equal(notes[0]?.querySelector("strong")?.textContent, "这里");
  assert.equal(notes[1]?.className, "lfv-note lfv-note-note");
  assert.equal(host.textContent?.includes("没闭合"), true);
});

test("Pandoc 的上下标和定义列表都认得出来", async () => {
  const dom = textFile("file:///tmp/a.md", "E=mc^2^ 和 H~2~O\n\n术语\n: 解释一句\n");
  const doc = dom.window.document;

  prettify(doc);
  const host = await rendered(doc);

  assert.equal(host.querySelector("sup")?.textContent, "2");
  assert.equal(host.querySelector("sub")?.textContent, "2");
  assert.equal(host.querySelector("dl dt")?.textContent, "术语");
  assert.equal(host.querySelector("dl dd")?.textContent, "解释一句");
  assert.equal(host.textContent?.includes("^"), false);
});

test("mdx 正文照常渲染，组件位置留一块写清楚的占位", async () => {
  const dom = textFile("file:///tmp/doc.mdx", "");
  const doc = dom.window.document;
  // 直接写 textContent，免得这些组件标签在造页面时就被 jsdom 当标签解析掉。
  first(doc).textContent =
    'import Chart from "./chart";\n\n# 标题\n\n<Chart data={[1, 2]} />\n\n一段正文。\n\n<Callout>\n提示\n</Callout>\n';

  prettify(doc);
  const host = await rendered(doc);

  const holes = host.querySelectorAll(".lfv-mdx");
  assert.equal(holes.length, 2);
  assert.equal(holes[0]?.querySelector("code")?.textContent, "Chart");
  assert.equal(holes[1]?.querySelector("code")?.textContent, "Callout");
  assert.equal(host.querySelector("h1")?.textContent?.startsWith("标题"), true);
  assert.equal(host.textContent?.includes("一段正文。"), true);
  // import 那行不该漏到正文里。
  assert.equal(host.textContent?.includes("./chart"), false);
});

/** 造一张 markdown 页并等它渲染完。 */
async function mdPage(text: string, url = "file:///tmp/doc.md") {
  const dom = textFile(url, "");
  const doc = dom.window.document;
  // 直接写 textContent，免得 markdown 源码在造页面时就被 jsdom 当标签解析掉。
  first(doc).textContent = text;
  prettify(doc);
  return { doc, host: await rendered(doc) };
}

test("标了 mermaid 的代码块画成图，不再走高亮那条路", async () => {
  const { host } = await mdPage("# 流程\n\n```mermaid\ngraph TD;\nA-->B;\n```\n");

  assert.equal(host.querySelector(".lfv-diagram svg")?.getAttribute("id"), "lfv-diagram-0");
  assert.equal(host.querySelector(".lfv-diagram title")?.textContent, "graph TD;");
  // 图取代了原来那个代码块。
  assert.equal(host.querySelector("code.language-mermaid"), null);
  assert.equal(asked.includes("mermaid.js"), true);
  assert.equal(asked.includes("highlight.js"), false);
});

test("图的语法有错时留下原文加一句错在哪，整篇照常", async () => {
  const { host } = await mdPage("```mermaid\n这行是错的\n```\n\n后面还有正文。\n");

  const box = host.querySelector(".lfv-diagram-error");
  assert.equal(box?.querySelector("pre")?.textContent, "这行是错的\n");
  assert.equal(box?.querySelector("p")?.textContent, "这张图画不出来：解析失败");
  assert.equal(host.textContent?.includes("后面还有正文。"), true);
});

test("图上有「查看大图」，点开全屏预览，Esc 关闭", async () => {
  const { doc, host } = await mdPage("```mermaid\ngraph TD;\nA-->B;\n```\n");

  let bubbled = false;
  doc.addEventListener("click", () => {
    bubbled = true;
  });
  const view = host.querySelector<HTMLButtonElement>(".lfv-diagram-view");
  assert.equal(view?.textContent, "查看大图");
  view?.click();

  const win = doc.defaultView!;
  const lightbox = doc.querySelector(".lfv-lightbox");
  assert.ok(lightbox, "点开后应有全屏预览");
  assert.equal(bubbled, false, "查看大图不应把点击继续交给外层页面");
  assert.equal(lightbox.querySelectorAll(":scope .lfv-lightbox-inner svg").length, 1, "预览里是那张图");
  // 按钮本身不能混进预览的图里。
  assert.equal(lightbox.querySelector(".lfv-diagram-view"), null);
  assert.equal(lightbox.querySelectorAll(".lfv-lightbox-btn").length, 4, "放大/缩小/重置/关闭");

  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(doc.querySelector(".lfv-lightbox"), null, "Esc 应关闭预览");
});

test("预览里滚轮缩放、拖动平移都会体现在 transform 上", async () => {
  const { doc, host } = await mdPage("```mermaid\ngraph TD;\nA-->B;\n```\n");
  const win = doc.defaultView!;
  host.querySelector<HTMLButtonElement>(".lfv-diagram-view")?.click();

  const inner = doc.querySelector<HTMLElement>(".lfv-lightbox-inner")!;
  const stage = doc.querySelector<HTMLElement>(".lfv-lightbox-stage")!;
  const zoom = doc.querySelector(".lfv-lightbox-zoom")!;
  const before = inner.style.transform;

  // jsdom 没有 WheelEvent 构造器时退回 MouseEvent——处理器只读 clientX/clientY/deltaY。
  const wheelCtor = (win as { WheelEvent?: typeof WheelEvent }).WheelEvent ?? win.MouseEvent;
  stage.dispatchEvent(new wheelCtor("wheel", { cancelable: true, clientX: 100, clientY: 100 } as WheelEventInit));
  assert.notEqual(inner.style.transform, before, "滚轮后 transform 应变化");
  assert.ok(zoom.textContent?.endsWith("%"));

  const pointerCtor = (win as { PointerEvent?: typeof PointerEvent }).PointerEvent ?? win.MouseEvent;
  const xBefore = Number(/translate\((-?[\d.]+)px/.exec(inner.style.transform)?.[1] ?? 0);
  stage.dispatchEvent(new pointerCtor("pointerdown", { button: 0, clientX: 50, clientY: 50 }));
  stage.dispatchEvent(new pointerCtor("pointermove", { clientX: 150, clientY: 170 }));
  stage.dispatchEvent(new pointerCtor("pointerup", { clientX: 150, clientY: 170 }));
  // 平移位移 = pointerdown 到 pointermove 的屏幕距离（50→150 即 100px），与缩放基准无关
  const m = /translate\((-?[\d.]+)px/.exec(inner.style.transform);
  assert.ok(m, `transform 里有 translate: ${inner.style.transform}`);
  assert.ok(Math.abs(Number(m[1]) - xBefore - 100) < 0.001,
    `拖 100px 应平移 100px，基准 ${xBefore}，实际 ${m[1]}`);

  // 点背景关闭；点图本身不关。
  const backdrop = doc.querySelector<HTMLElement>(".lfv-lightbox")!;
  stage.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  assert.ok(doc.querySelector(".lfv-lightbox"), "点到图不该关");
  backdrop.dispatchEvent(new win.MouseEvent("click"));
  assert.equal(doc.querySelector(".lfv-lightbox"), null, "点背景应关闭");
});


test("行内和独立成行的公式都排版，样式表也挂上", async () => {
  const { doc, host } = await mdPage("能量是 $E = mc^2$ 这么来的。\n\n$$\n\\sum_{i=1}^n i\n$$\n");

  const inline = host.querySelector("span.lfv-math");
  const block = host.querySelector("div.lfv-math.lfv-math-block");
  assert.equal(inline?.textContent, "行内公式:E = mc^2");
  assert.equal(block?.textContent, "块公式:\\sum_{i=1}^n i");
  assert.equal(
    doc.querySelector("link#lfv-katex-style")?.getAttribute("href"),
    "chrome-extension://viewer/katex.css",
  );
  assert.equal(asked.includes("katex.js"), true);
});

test("美元金额不当成公式", async () => {
  const { host } = await mdPage("这本书卖 $ 5，那本 $ 8。\n");

  assert.equal(host.querySelector(".lfv-math"), null);
  assert.equal(asked.includes("katex.js"), false);
});

test("文档里没有图也没有公式时，这两个包都不加载", async () => {
  const { host } = await mdPage("# 标题\n\n一段普通正文。\n");

  assert.equal(host.querySelector(".lfv-md")?.textContent?.includes("一段普通正文。"), true);
  assert.equal(asked.includes("mermaid.js"), false);
  assert.equal(asked.includes("katex.js"), false);
});

/**
 * 造一张带链接的 markdown 文件页（`file://`），并把两件跟外界打交道的事换成可观察的桩：
 * `fetch` 和 `window.open`。两个都不该再被用到——文件页上的链接一个都不拦。
 *
 * `click()` 返回 `true` 表示没有人调过 `preventDefault`，也就是这一下交给了浏览器自己。
 */
async function linkPage(text: string) {
  const dom = textFile("file:///tmp/docs/guide.md", "");
  const doc = dom.window.document;
  // 直接写 textContent，免得 markdown 源码在造页面时就被 jsdom 当标签解析掉。
  first(doc).textContent = text;

  const opened: string[] = [];
  const probed: string[] = [];
  (dom.window as unknown as { open: unknown }).open = (url: string, target: string) => {
    opened.push(`${url} ${target}`);
    return null;
  };
  (globalThis as { fetch?: unknown }).fetch = async (url: string) => {
    probed.push(url);
    return { ok: true } as Response;
  };
  prettify(doc);
  const host = await rendered(doc);
  return { doc, host, opened, probed, click: clicker(dom, host) };
}

/**
 * 造一张扩展自己的展示页（`chrome-extension://`），内容是某个本地 markdown 文件。
 *
 * 这一档里本地链接必须被拦下来交给后台脚本：Chrome 不让扩展页面自己导航到 `file://`。
 */
async function viewerPage(text: string) {
  const dom = page("", { url: "chrome-extension://viewer/viewer.html" });
  const doc = dom.window.document;
  show(doc, "file:///tmp/docs/guide.md", text);
  const host = await rendered(doc);
  return { doc, host, click: clicker(dom, host) };
}

/**
 * 点一个链接，返回「这一下有没有交给浏览器」：`true` = 没人拦，浏览器会照常导航。
 *
 * 判定看的是页面自己的监听跑完之后 `defaultPrevented` 的状态；拿到之后再统一 `preventDefault`，
 * 免得 jsdom 去做它没实现的导航、往控制台吐一行。
 */
function clicker(dom: JSDOM, host: HTMLElement) {
  return (href: string): boolean => {
    let prevented = false;
    const swallow = (event: Event) => {
      prevented = event.defaultPrevented;
      event.preventDefault();
    };
    host.ownerDocument.addEventListener("click", swallow);
    host
      .querySelector(`a[href="${href}"]`)
      ?.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    host.ownerDocument.removeEventListener("click", swallow);
    return !prevented;
  };
}

test("写成绝对地址的本地链接和图片都保得住，点得动", async () => {
  const { host } = await mdPage(
    "[文件](file:///tmp/docs/api.md)\n\n![图](file:///tmp/docs/a.png)\n",
  );

  // DOMPurify 默认那张表里没有 `file:`，不放行就会摘掉 href / src，
  // 渲染出来是个点不动的光秃秃 <a>——这正是本地文件链接点不开的第二个原因。
  assert.equal(
    host.querySelector("a[href='file:///tmp/docs/api.md']")?.textContent,
    "文件",
  );
  assert.equal(host.querySelector("img")?.getAttribute("src"), "file:///tmp/docs/a.png");
});

test("放行 file: 之后，javascript: 这类地址照样挡着", async () => {
  const { host } = await mdPage("[坏](javascript:alert(1))\n");

  assert.equal(host.querySelector("a")?.hasAttribute("href"), false);
});

test("文件页上的本地链接一个都不拦，交给浏览器自己跳", async () => {
  const { opened, probed, click } = await linkPage(
    "[同级](./api.md)\n\n[子目录](guide/x.md)\n\n[上层](../readme.md)\n",
  );

  // 三次点击都没有被 preventDefault，浏览器会照常导航；新页面还是 `file://`，
  // 内容脚本照样接管，所以前进后退回来仍是渲染好的样子。
  assert.deepEqual(
    [click("./api.md"), click("guide/x.md"), click("../readme.md")],
    [true, true, true],
  );
  await new Promise((resolve) => setTimeout(resolve, 20));

  // 关键回归：早先这里会先 fetch 探一下文件在不在，而内容脚本 fetch 不了 `file://`，
  // 于是每个本地链接都被判成「文件不存在」、点了打不开。现在一次都不该探。
  assert.deepEqual(probed, []);
  assert.deepEqual(opened, []);
  assert.deepEqual(sent, []);
});

test("文件页上，图片压缩包、网络地址、页内锚点同样不拦", async () => {
  const { opened, probed, click } = await linkPage(
    "# 安装\n\n[图](./a.png)\n\n[包](./b.zip)\n\n[网页](https://example.test/a.md)\n\n[回到安装](#安装)\n",
  );

  assert.deepEqual(
    [click("./a.png"), click("./b.zip"), click("https://example.test/a.md"), click("#安装")],
    [true, true, true, true],
  );
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(probed, []);
  assert.deepEqual(opened, []);
  assert.deepEqual(sent, []);
});

test("展示页上的本地链接交给后台脚本跳转", async () => {
  const { click } = await viewerPage("[同级](./api.md)\n\n[上层](../readme.md)\n");

  // 返回 false = 被 preventDefault 了：扩展页面自己导航到 `file://` 会被 Chrome 挡下，
  // 所以这一档必须拦，改由后台脚本用 chrome.tabs.update 去跳。
  assert.deepEqual([click("./api.md"), click("../readme.md")], [false, false]);
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(sent, [
    { type: "lfv-open", url: "file:///tmp/docs/api.md" },
    { type: "lfv-open", url: "file:///tmp/readme.md" },
  ]);
});

test("展示页上的网络链接和页内锚点不拦", async () => {
  const { click } = await viewerPage(
    "# 安装\n\n[网页](https://example.test/a.md)\n\n[回到安装](#安装)\n",
  );

  assert.deepEqual([click("https://example.test/a.md"), click("#安装")], [true, true]);
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(sent, []);
});

/** 等折叠树那一拍落地，并把页面和树一起给出来。 */
async function dataPage(url: string, text: string) {
  const dom = textFile(url, "");
  const doc = dom.window.document;
  first(doc).textContent = text;
  prettify(doc);
  for (let i = 0; i < 200; i += 1) {
    const host = doc.querySelector(".lfv-data.lfv-rendered");
    if (host) return { dom, doc, host: host as HTMLElement };
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到折叠树渲染完成");
}

test("json 展开成可折叠的树，对象和数组报出元素个数", async () => {
  const { host } = await dataPage(
    "file:///tmp/a.json",
    '{"name":"x","list":[1,2,3],"deep":{"on":true}}',
  );

  const root = host.querySelector("details.lfv-node") as HTMLDetailsElement;
  assert.equal(root.querySelector("summary .lfv-count")?.textContent, "{…} 3 项");
  assert.equal(root.open, true);
  // 每个键一行，数组自己那行报 3 项。
  const counts = Array.from(host.querySelectorAll(".lfv-count"), (n) => n.textContent);
  assert.deepEqual(counts, ["{…} 3 项", "[…] 3 项", "{…} 1 项"]);
  // 折叠是浏览器自带的：把 open 摘掉就收起来了，没有额外的 JS。
  root.open = false;
  assert.equal(root.open, false);
});

test("不同数据类型用不同的类名上色", async () => {
  const { host } = await dataPage(
    "file:///tmp/a.json",
    '{"s":"字","n":1.5,"b":false,"z":null}',
  );

  const typed = Array.from(host.querySelectorAll(".lfv-tree span[class^='lfv-']"))
    .filter((n) => !n.classList.contains("lfv-key") && !n.classList.contains("lfv-count"))
    .map((n) => [n.className, n.textContent]);
  assert.deepEqual(typed, [
    ["lfv-str", '"字"'],
    ["lfv-num", "1.5"],
    ["lfv-bool", "false"],
    ["lfv-null", "null"],
  ]);
});

test("点节点的键复制它的路径，复制完给出反馈", async () => {
  const { dom, host } = await dataPage("file:///tmp/a.json", '{"a":{"list":[10]}}');
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

  const keys = host.querySelectorAll(".lfv-key");
  assert.deepEqual(
    Array.from(keys, (k) => (k as HTMLElement).dataset["path"]),
    ["$.a", "$.a.list", "$.a.list[0]"],
  );

  const leaf = keys[2] as HTMLElement;
  const before = leaf.textContent;
  leaf.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(copied, "$.a.list[0]");
  assert.equal(leaf.textContent, "已复制 ");
  assert.notEqual(before, leaf.textContent);
});

test("yaml 走同一棵树", async () => {
  const { host } = await dataPage("file:///tmp/a.yaml", "name: x\nlist:\n  - 1\n  - 2\n");

  assert.equal(host.querySelector(".lfv-count")?.textContent, "{…} 2 项");
  assert.equal(host.querySelector(".lfv-str")?.textContent, '"x"');
  assert.equal(host.querySelectorAll(".lfv-num").length, 2);
});

test("json 语法有错时说清第几行错在哪，原文照常排版出来", async () => {
  const { host } = await dataPage("file:///tmp/bad.json", '{\n  "a": 1,\n  "b": ,\n}\n');

  const note = host.querySelector(".lfv-parse-error");
  assert.equal(note?.textContent?.startsWith("第 3 行有语法错误："), true);
  // 原文一个字不少地留在下面。
  assert.equal(host.querySelector(".lfv-code .lfv-text")?.textContent, '{\n  "a": 1,\n  "b": ,\n}\n');
  assert.equal(host.querySelector(".lfv-tree"), null);

  // 另一种报法（解析器自己就带行号）同样落在出错那一行。
  const missingComma = await dataPage("file:///tmp/bad2.json", '{\n  "a": 1\n  "b": 2\n}\n');
  assert.equal(
    missingComma.host.querySelector(".lfv-parse-error")?.textContent?.startsWith("第 3 行"),
    true,
  );

  // 连位置都报不出来的（文件截断）退到第 1 行，原文照常摆出来。
  const truncated = await dataPage("file:///tmp/bad3.json", '{\n  "a": [1,\n');
  assert.equal(
    truncated.host.querySelector(".lfv-parse-error")?.textContent?.startsWith("第 1 行"),
    true,
  );
  assert.equal(truncated.host.querySelector(".lfv-code .lfv-text")?.textContent, '{\n  "a": [1,\n');
});

test("yaml 语法有错时同样报行号，原文照常排版出来", async () => {
  const { host } = await dataPage("file:///tmp/bad.yaml", "a: 1\nb: [1, 2\nc: 3\n");

  const note = host.querySelector(".lfv-parse-error");
  assert.equal(note?.textContent?.startsWith("第 "), true);
  assert.equal(note?.textContent?.includes("行有语法错误："), true);
  assert.equal(host.querySelector(".lfv-code .lfv-text")?.textContent, "a: 1\nb: [1, 2\nc: 3\n");
});

test("空文件、单个标量、深层嵌套都不报错", async () => {
  const empty = await dataPage("file:///tmp/empty.yaml", "");
  assert.equal(empty.host.querySelector(".lfv-null")?.textContent, "null");
  assert.equal(empty.host.querySelector(".lfv-parse-error"), null);

  const one = await dataPage("file:///tmp/one.json", '"只有一个字符串"');
  assert.equal(one.host.querySelector(".lfv-str")?.textContent, '"只有一个字符串"');

  // 20 层嵌套，最里面那个值仍然在树上，路径也一路带下来。
  const depth = 20;
  const deep = '{"k":'.repeat(depth) + "1" + "}".repeat(depth);
  const nested = await dataPage("file:///tmp/deep.json", deep);
  const keys = nested.host.querySelectorAll(".lfv-key");
  assert.equal(keys.length, depth);
  assert.equal((keys[depth - 1] as HTMLElement).dataset["path"], `$${".k".repeat(depth)}`);
  assert.equal(nested.host.querySelector(".lfv-num")?.textContent, "1");
});

/** 造一张 csv 页并等表格建好。 */
async function csvPage(text: string, url = "file:///tmp/a.csv") {
  const dom = textFile(url, "");
  const doc = dom.window.document;
  first(doc).textContent = text;
  prettify(doc);
  for (let i = 0; i < 200; i += 1) {
    const host = doc.querySelector(".lfv-csv.lfv-rendered");
    if (host) return { dom, doc, host: host as HTMLElement };
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到表格渲染完成");
}

/** 表格正文，一行一个数组。 */
const cells = (host: HTMLElement) =>
  Array.from(host.querySelectorAll("tbody tr"), (tr) =>
    Array.from(tr.querySelectorAll("td"), (td) => td.textContent),
  );

test("csv 第一行当表头，其余是正文", async () => {
  const { host } = await csvPage("名字,数量\n苹果,3\n梨,10\n");

  assert.deepEqual(
    Array.from(host.querySelectorAll("thead th .lfv-th-label"), (n) => n.textContent),
    ["名字", "数量"],
  );
  assert.deepEqual(cells(host), [
    ["苹果", "3"],
    ["梨", "10"],
  ]);
});

test("点表头排序，再点一次反向；数字列按数值大小排", async () => {
  const { dom, host } = await csvPage("名字,数量\n苹果,3\n梨,10\n桃,2\n");
  const click = (i: number) =>
    (host.querySelectorAll("thead th .lfv-th-label")[i] as HTMLElement).dispatchEvent(
      new dom.window.MouseEvent("click", { bubbles: true }),
    );

  click(1);
  // 按数值：2 < 3 < 10。按字符会排成 10、2、3。
  assert.deepEqual(cells(host).map((r) => r[1]), ["2", "3", "10"]);
  assert.equal(host.querySelectorAll("thead th")[1]?.getAttribute("data-sort"), "asc");

  click(1);
  assert.deepEqual(cells(host).map((r) => r[1]), ["10", "3", "2"]);
  assert.equal(host.querySelectorAll("thead th")[1]?.getAttribute("data-sort"), "desc");

  // 换一列排，文字列按文字排，上一列的箭头让出来。
  click(0);
  assert.deepEqual(cells(host).map((r) => r[0]), ["桃", "梨", "苹果"].sort((a, b) => a.localeCompare(b)));
  assert.equal(host.querySelectorAll("thead th")[1]?.hasAttribute("data-sort"), false);
});

test("拖列边界改列宽", async () => {
  const { dom, host } = await csvPage("名字,数量\n苹果,3\n");
  const th = host.querySelectorAll("thead th")[0] as HTMLElement;
  // jsdom 没有排版，自己给这一列一个初始宽度。
  Object.defineProperty(th, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ width: 100 }) as DOMRect,
  });
  const grip = th.querySelector(".lfv-grip") as HTMLElement;
  const at = (type: string, x: number) =>
    new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x });

  grip.dispatchEvent(at("mousedown", 200));
  dom.window.document.dispatchEvent(at("mousemove", 260));
  assert.equal(th.style.width, "160px");

  // 拖没了也留得住：最窄 40px。
  dom.window.document.dispatchEvent(at("mousemove", 0));
  assert.equal(th.style.width, "40px");

  // 松手之后再动鼠标就不改宽度了。
  dom.window.document.dispatchEvent(at("mouseup", 0));
  dom.window.document.dispatchEvent(at("mousemove", 400));
  assert.equal(th.style.width, "40px");

  // 拖把手那一下不触发排序。
  assert.equal(host.querySelector("thead th")?.hasAttribute("data-sort"), false);
});

test("带引号的字段、字段里的逗号和换行都切对", async () => {
  const { host } = await csvPage('a,b\n"含,逗号","含\n换行"\n"带""引号""的",末尾\n');

  assert.deepEqual(cells(host), [
    ["含,逗号", "含\n换行"],
    ['带"引号"的', "末尾"],
  ]);
});

test("列数不齐的行照样渲染，缺格留空并标出来", async () => {
  const { host } = await csvPage("a,b,c\n1,2\n1,2,3,4\n");

  assert.deepEqual(cells(host), [
    ["1", "2", ""],
    ["1", "2", "3"],
  ]);
  const rows = host.querySelectorAll("tbody tr");
  assert.equal(rows[0]?.className, "lfv-row-ragged");
  assert.equal(rows[0]?.querySelectorAll(".lfv-cell-missing").length, 1);
  assert.equal(rows[1]?.className, "lfv-row-ragged");
});

test("空文件和只有表头的文件都不报错", async () => {
  const empty = await csvPage("");
  assert.equal(empty.host.querySelectorAll("thead th").length, 0);
  assert.equal(empty.host.querySelectorAll("tbody tr").length, 0);

  const headOnly = await csvPage("a,b\n");
  assert.equal(headOnly.host.querySelectorAll("thead th").length, 2);
  assert.equal(headOnly.host.querySelectorAll("tbody tr").length, 0);
});

test("类型分档按扩展名，带 query 和 hash 也认得出来", () => {
  assert.equal(classify("file:///tmp/a.md"), "whitelist");
  assert.equal(classify("file:///tmp/a.yaml?x=1#y"), "whitelist");
  assert.equal(classify("file:///tmp/a.svg"), "webpage");
  assert.equal(classify("file:///tmp/README"), "unknown");
  assert.equal(classify("file:///tmp/.bashrc"), "unknown");
});

/** 造一张日志页并等它渲染完。 */
async function logPage(text: string, url = "file:///tmp/a.log") {
  const dom = textFile(url, "");
  const doc = dom.window.document;
  first(doc).textContent = text;
  prettify(doc);
  for (let i = 0; i < 200; i += 1) {
    const host = doc.querySelector(".lfv-logs.lfv-rendered");
    if (host) return { dom, doc, host: host as HTMLElement };
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到日志渲染完成");
}

/** 一行日志拆出来的三列。 */
const columns = (row: Element) =>
  [".lfv-log-time", ".lfv-log-level", ".lfv-log-message"].map(
    (sel) => row.querySelector(sel)?.textContent,
  );

test("常见格式的日志行按级别上色，时间、级别、消息各自成列", async () => {
  const { host } = await logPage(
    "2026-09-16T03:04:05.123Z INFO 启动完成\n" +
      "[2026-09-16 03:04:06] ERROR 连不上数据库\n" +
      "2026-09-16 03:04:07 WARNING 重试中\n",
  );

  const rows = host.querySelectorAll(".lfv-log-row");
  assert.equal(rows.length, 3);
  assert.deepEqual(columns(rows[0] as Element), [
    "2026-09-16T03:04:05.123Z",
    "info",
    "INFO 启动完成",
  ]);
  assert.equal(rows[0]?.className, "lfv-log-row lfv-log-info");
  assert.equal(rows[1]?.className, "lfv-log-row lfv-log-error");
  // WARNING 归到 warn，同一个类名。
  assert.equal(rows[2]?.className, "lfv-log-row lfv-log-warn");
  assert.equal((rows[1] as Element).querySelector(".lfv-log-time")?.textContent, "2026-09-16 03:04:06");
});

test("关掉某一级后对应行隐藏，再打开恢复；只列出文件里出现过的级别", async () => {
  const { dom, host } = await logPage("INFO 一\nERROR 二\nINFO 三\n");
  const view = host.querySelector(".lfv-log") as HTMLElement;
  const boxes = Array.from(
    host.querySelectorAll(".lfv-log-filters input"),
    (n) => n as HTMLInputElement,
  );

  assert.deepEqual(
    boxes.map((b) => b.dataset["level"]),
    ["error", "info"],
  );
  assert.ok(boxes.every((b) => b.checked));

  const toggleBox = (box: HTMLInputElement, checked: boolean) => {
    box.checked = checked;
    box.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  };

  const info = boxes[1] as HTMLInputElement;
  toggleBox(info, false);
  assert.ok(view.classList.contains("lfv-hide-info"));
  assert.equal(view.classList.contains("lfv-hide-error"), false);

  toggleBox(info, true);
  assert.equal(view.classList.contains("lfv-hide-info"), false);
});

test("整行为 JSON 的日志行可展开成折叠树", async () => {
  const { host } = await logPage(
    '{"time":"2026-09-16T03:04:05Z","level":"error","msg":"炸了","code":500}\n普通一行\n',
  );

  const rows = host.querySelectorAll(".lfv-log-row");
  assert.equal(rows[0]?.className, "lfv-log-row lfv-log-error");
  assert.deepEqual(
    [
      (rows[0] as Element).querySelector(".lfv-log-time")?.textContent,
      (rows[0] as Element).querySelector(".lfv-log-level")?.textContent,
    ],
    ["2026-09-16T03:04:05Z", "error"],
  );

  const message = (rows[0] as Element).querySelector(".lfv-log-message") as HTMLElement;
  assert.equal(message.tagName, "DETAILS");
  assert.ok(message.querySelector(".lfv-tree"), "JSON 行里应该有一棵树");
  assert.equal(message.querySelector("summary")?.textContent?.includes("炸了"), true);

  // 第二行不是 JSON，仍是普通一行。
  assert.equal((rows[1] as Element).querySelector(".lfv-log-message")?.tagName, "SPAN");
});

test("一行 JSON 都没有时不去加载树那个包", async () => {
  await logPage("INFO 一\nERROR 二\n");
  assert.equal(asked.includes("data.js"), false);
  assert.equal(asked.includes("log.js"), true);
});

test("格式认不出来的行原样显示，也不被任何过滤开关藏起来", async () => {
  const { dom, host } = await logPage("一段谁也认不出来的话\nERROR 二\n");

  const rows = host.querySelectorAll(".lfv-log-row");
  assert.equal(rows[0]?.className, "lfv-log-row");
  assert.deepEqual(columns(rows[0] as Element), ["", "", "一段谁也认不出来的话"]);

  // 把出现过的级别全关掉，这一行仍然没有任何级别类名，CSS 也就碰不到它。
  const view = host.querySelector(".lfv-log") as HTMLElement;
  for (const node of host.querySelectorAll(".lfv-log-filters input")) {
    const box = node as HTMLInputElement;
    box.checked = false;
    box.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  }
  assert.ok(view.classList.contains("lfv-hide-error"));
  assert.equal(
    Array.from(rows[0]?.classList ?? []).some((c) => c.startsWith("lfv-log-") && c !== "lfv-log-row"),
    false,
  );
});

test("空文件和超长单行都不报错", async () => {
  const empty = await logPage("");
  assert.equal(empty.host.querySelectorAll(".lfv-log-row").length, 0);
  assert.equal(empty.host.querySelectorAll(".lfv-log-filters input").length, 0);

  const long = "INFO " + "x".repeat(200_000);
  const huge = await logPage(`${long}\n`);
  const rows = huge.host.querySelectorAll(".lfv-log-row");
  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.querySelector(".lfv-log-message")?.textContent?.length, long.length);
});

/** 一条目录项，照浏览器索引页原样拼：名字格一个 `<a>`，大小和时间把原始数值放 `data-value`。 */
function indexRow(name: string, dir: boolean, size: number, mtime: number, date: string) {
  const shown = dir ? `${name}/` : name;
  return (
    `<tr><td data-value="${name}"><a class="icon ${dir ? "dir" : "file"}" href="${shown}">${shown}</a></td>` +
    `<td class="detailsColumn" data-value="${size}">${dir ? "" : `${size} B`}</td>` +
    `<td class="detailsColumn" data-value="${mtime}">${date}</td></tr>`
  );
}

/** 造一张浏览器的本地目录索引页。 */
function indexPage(rows: string, url = "file:///tmp/dir/") {
  const dom = page(
    '<h1 id="header">Index of /tmp/dir/</h1>' +
      '<table><thead><tr class="header" id="theader">' +
      '<th id="nameColumnHeader">Name</th><th class="detailsColumn">Size</th>' +
      '<th class="detailsColumn">Date Modified</th></tr></thead>' +
      `<tbody id="tbody">${rows}</tbody></table>`,
    { url, contentType: "text/html" },
  );
  dom.window.document.title = "/tmp/dir/";
  return dom;
}

/** 造目录页并等列表建好。 */
async function dirPage(rows: string, url = "file:///tmp/dir/") {
  const dom = indexPage(rows, url);
  const doc = dom.window.document;
  prettify(doc);
  for (let i = 0; i < 200; i += 1) {
    const host = doc.querySelector(".lfv-dir.lfv-rendered");
    if (host) return { dom, doc, host: host as HTMLElement };
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到目录列表渲染完成");
}

const SAMPLE =
  indexRow("src", true, 0, 300, "2026/9/14 10:00:00") +
  indexRow("b.go", false, 30, 200, "2026/9/15 10:00:00") +
  indexRow("a.png", false, 500, 100, "2026/9/16 10:00:00") +
  indexRow(".env", false, 5, 400, "2026/9/13 10:00:00");

/** 表格里看得见的条目名（不含被 CSS 藏起来的隐藏文件，那一档单独测）。 */
const names = (host: HTMLElement) =>
  Array.from(host.querySelectorAll("tbody tr .lfv-entry"), (n) => n.textContent);

test("打开本地目录时换成美化后的列表，条目按类型给图标", async () => {
  const { doc, host } = await dirPage(SAMPLE);

  assert.ok(doc.documentElement.classList.contains("lfv-on"));
  assert.equal(doc.querySelector("#tbody"), null, "浏览器那张表已经换掉了");
  assert.deepEqual(names(host), [".env", "a.png", "b.go", "src/"]);

  const icon = (name: string) =>
    Array.from(host.querySelectorAll(".lfv-entry")).find((n) => n.textContent === name)?.className;
  assert.equal(icon("src/"), "lfv-entry lfv-icon-dir");
  assert.equal(icon("b.go"), "lfv-entry lfv-icon-code");
  assert.equal(icon("a.png"), "lfv-entry lfv-icon-image");
});

test("切回原始拿回浏览器那张索引表", async () => {
  const { doc } = await dirPage(SAMPLE);

  toggle(doc).click();
  assert.equal(doc.documentElement.classList.contains("lfv-on"), false);
  assert.equal(doc.querySelectorAll("#tbody tr").length, 4);

  toggle(doc).click();
  assert.ok(doc.querySelector(".lfv-dir-table"));
});

test("名称、大小、修改时间三种排序可用且可反向", async () => {
  const { dom, host } = await dirPage(SAMPLE);
  const click = (i: number) =>
    (host.querySelectorAll("thead th")[i] as HTMLElement).dispatchEvent(
      new dom.window.MouseEvent("click", { bubbles: true }),
    );

  click(1); // 大小：0 < 5 < 30 < 500
  assert.deepEqual(names(host), ["src/", ".env", "b.go", "a.png"]);
  assert.equal(host.querySelectorAll("thead th")[1]?.getAttribute("data-sort"), "asc");

  click(1);
  assert.deepEqual(names(host), ["a.png", "b.go", ".env", "src/"]);
  assert.equal(host.querySelectorAll("thead th")[1]?.getAttribute("data-sort"), "desc");

  click(2); // 时间：100 < 200 < 300 < 400
  assert.deepEqual(names(host), ["a.png", "b.go", "src/", ".env"]);
  assert.equal(host.querySelectorAll("thead th")[1]?.hasAttribute("data-sort"), false);

  click(0);
  assert.deepEqual(names(host), [".env", "a.png", "b.go", "src/"]);
});

test("过滤框随输入实时筛选条目", async () => {
  const { dom, host } = await dirPage(SAMPLE);
  const box = host.querySelector(".lfv-dir-filter") as HTMLInputElement;
  const visible = () =>
    Array.from(host.querySelectorAll("tbody tr"))
      .filter((tr) => !(tr as HTMLElement).hidden)
      .map((tr) => (tr as HTMLElement).dataset["name"]);

  box.value = "o";
  box.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.deepEqual(visible(), ["b.go"]);

  // 大小写不论，清空恢复全部。
  box.value = "A.PNG";
  box.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.deepEqual(visible(), ["a.png"]);

  box.value = "";
  box.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(visible().length, 4);
});

test("面包屑显示当前层级，点任意一层跳过去", async () => {
  const { host } = await dirPage(SAMPLE, "file:///tmp/dir/");

  assert.deepEqual(
    Array.from(host.querySelectorAll(".lfv-crumb"), (n) => [
      n.textContent,
      n.getAttribute("href"),
    ]),
    [
      ["/", "file:///"],
      ["tmp", "file:///tmp/"],
      ["dir", "file:///tmp/dir/"],
    ],
  );
});

test("隐藏文件默认收起，开关打开才摊出来", async () => {
  const { dom, host } = await dirPage(SAMPLE);
  const view = host.querySelector(".lfv-dir-view") as HTMLElement;
  const dot = host.querySelector("tbody tr.lfv-dot") as HTMLElement;

  // 藏不藏交给 CSS：行标着 `lfv-dot`，容器默认没有 `lfv-show-hidden`。
  assert.equal(dot.dataset["name"], ".env");
  assert.equal(view.classList.contains("lfv-show-hidden"), false);

  const box = host.querySelector(".lfv-dir-dotfiles input") as HTMLInputElement;
  box.checked = true;
  box.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.ok(view.classList.contains("lfv-show-hidden"));

  box.checked = false;
  box.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(view.classList.contains("lfv-show-hidden"), false);
});

test("悬停文本文件浮出开头几行，图片这类不触发", async () => {
  const { dom, host } = await dirPage(SAMPLE);
  let fetched: string | null = null;
  globalThis.fetch = (async (url: string) => {
    fetched = url;
    return { text: async () => "1\n2\n3\n4\n5\n6\n7\n" };
  }) as unknown as typeof fetch;

  const hover = (name: string) =>
    (Array.from(host.querySelectorAll(".lfv-entry")).find(
      (n) => n.textContent === name,
    ) as HTMLElement).dispatchEvent(new dom.window.MouseEvent("mouseenter"));

  hover("b.go");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const preview = host.querySelector(".lfv-preview") as HTMLElement;
  // 只要开头 5 行。
  assert.equal(preview.textContent, "1\n2\n3\n4\n5");
  assert.equal(String(fetched), "file:///tmp/dir/b.go");

  // 移开收起，再回来不重读。
  (
    Array.from(host.querySelectorAll(".lfv-entry")).find(
      (n) => n.textContent === "b.go",
    ) as HTMLElement
  ).dispatchEvent(new dom.window.MouseEvent("mouseleave"));
  assert.equal(preview.hidden, true);

  fetched = null;
  hover("b.go");
  assert.equal(preview.hidden, false);
  assert.equal(fetched, null);

  // 图片不是文本，根本不去读。
  hover("a.png");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(host.querySelectorAll(".lfv-preview").length, 1);
  assert.equal(fetched, null);
});

test("空目录不报错，直说这个目录是空的", async () => {
  const { host } = await dirPage("");

  assert.equal(host.querySelectorAll("tbody tr").length, 0);
  assert.equal(host.querySelector(".lfv-dir-empty")?.textContent, "这个目录是空的");
});

test("不是目录索引页的页面不被这条判定误接管", () => {
  // 普通网页里就算有一张 id 相同的表，也不是 file:// 上的索引页。
  const web = page('<table><tr id="theader"></tr><tbody id="tbody"></tbody></table>', {
    url: "https://example.test/list",
  });
  assert.equal(isDirectoryIndex(web.window.document), false);

  // 本地的普通 html 文件没有那两个 id。
  const local = page("<table><tbody></tbody></table>", { url: "file:///tmp/a.html" });
  assert.equal(isDirectoryIndex(local.window.document), false);

  // 文本文件页也不会被误判。
  const text = textFile("file:///tmp/a.go", "package main");
  assert.equal(isDirectoryIndex(text.window.document), false);

  assert.equal(isDirectoryIndex(indexPage("").window.document), true);
});

/** 按一次查找快捷键。`meta` 是 macOS 的 Cmd，`ctrl` 是其他平台。 */
function find(doc: Document): void {
  const view = doc.defaultView as Window & typeof globalThis;
  doc.dispatchEvent(new view.KeyboardEvent("keydown", { key: "f", metaKey: true, bubbles: true }));
}

/** 在搜索框里按一个键。事件从输入框冒上去，和真人按键一样。 */
function press(bar: HTMLElement, key: string, shiftKey = false): void {
  const input = bar.querySelector("input") as HTMLInputElement;
  const view = (bar.ownerDocument.defaultView as Window & typeof globalThis);
  input.dispatchEvent(new view.KeyboardEvent("keydown", { key, shiftKey, bubbles: true }));
}

/** 往搜索框里输入一个词，等它把命中标出来。 */
function type(bar: HTMLElement, needle: string): void {
  const input = bar.querySelector("input") as HTMLInputElement;
  const view = bar.ownerDocument.defaultView as Window & typeof globalThis;
  input.value = needle;
  input.dispatchEvent(new view.Event("input"));
}

/** 等搜索框那一拍落地：它那个包是第一次按快捷键才去加载的。 */
async function searchBar(doc: Document): Promise<HTMLElement> {
  for (let i = 0; i < 200; i += 1) {
    const bar = doc.querySelector(".lfv-search");
    if (bar) return bar as HTMLElement;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("等不到搜索框出现");
}

/** 打开一个已经美化好的文本页，并把搜索框叫出来。 */
async function searchPage(text: string): Promise<{ doc: Document; bar: HTMLElement }> {
  const dom = textFile("file:///tmp/notes.txt", text);
  const doc = dom.window.document;
  prettify(doc);
  find(doc);
  return { doc, bar: await searchBar(doc) };
}

const counter = (bar: HTMLElement) => bar.querySelector(".lfv-search-count")?.textContent;

test("按查找快捷键弹出扩展自己的搜索框，命中全部高亮，当前一处颜色不同", async () => {
  const { doc, bar } = await searchPage("alpha beta alpha gamma alpha");
  assert.equal(asked.includes("search.js"), true);

  type(bar, "alpha");

  const hits = doc.querySelectorAll(".lfv-hit");
  assert.equal(hits.length, 3);
  assert.equal(doc.querySelectorAll(".lfv-hit-current").length, 1);
  assert.equal(hits[0]?.classList.contains("lfv-hit-current"), true);
  assert.equal(counter(bar), "第 1 个 / 共 3 个");
});

test("回车和上下箭头在命中之间循环跳", async () => {
  const { doc, bar } = await searchPage("x1 x2 x3");
  type(bar, "x");

  const current = () => doc.querySelector(".lfv-hit-current")?.parentElement?.textContent;
  assert.equal(counter(bar), "第 1 个 / 共 3 个");

  press(bar, "Enter");
  assert.equal(counter(bar), "第 2 个 / 共 3 个");
  press(bar, "ArrowDown");
  assert.equal(counter(bar), "第 3 个 / 共 3 个");

  // 最后一个再往下回到第一个。
  press(bar, "Enter");
  assert.equal(counter(bar), "第 1 个 / 共 3 个");
  // 第一个往上回到最后一个。
  press(bar, "ArrowUp");
  assert.equal(counter(bar), "第 3 个 / 共 3 个");
  press(bar, "Enter", true);
  assert.equal(counter(bar), "第 2 个 / 共 3 个");
  assert.ok(current()?.includes("x1 x2 x3"));
});

test("命中落在折叠起来的节点里时自动展开", async () => {
  const { doc, bar } = await searchPage("外面");
  const details = doc.createElement("details");
  details.innerHTML = "<summary>折起来的</summary><p>里面藏着 needle</p>";
  doc.body.append(details);
  assert.equal(details.open, false);

  type(bar, "needle");

  assert.equal(details.open, true);
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 1);
});

test("Esc 关掉搜索框，高亮全部清掉，文本回到原样", async () => {
  const { doc, bar } = await searchPage("alpha beta alpha");
  const before = (doc.querySelector(".lfv-text") as HTMLElement).innerHTML;

  type(bar, "alpha");
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 2);

  press(bar, "Escape");

  assert.equal(doc.querySelector(".lfv-search"), null);
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 0);
  assert.equal((doc.querySelector(".lfv-text") as HTMLElement).innerHTML, before);
});

test("没有命中时明说没有找到，词清空后提示也跟着消失", async () => {
  const { doc, bar } = await searchPage("alpha beta");

  type(bar, "zzz");
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 0);
  assert.equal(counter(bar), "没有找到");

  type(bar, "");
  assert.equal(counter(bar), "");
});

test("已经开着的搜索框不会再开第二个", async () => {
  const { doc } = await searchPage("alpha");

  find(doc);
  await new Promise((resolve) => setTimeout(resolve, 5));

  assert.equal(doc.querySelectorAll(".lfv-search").length, 1);
});

test("没被美化的页面不接管查找快捷键", async () => {
  const dom = textFile("file:///tmp/data.unknownext", "alpha");
  const doc = dom.window.document;
  prettify(doc);

  // 这一档只挂了「美化一下」按钮，快捷键应当留给浏览器自己。
  find(doc);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(doc.querySelector(".lfv-search"), null);
  assert.equal(asked.includes("search.js"), false);

  // 美化之后才接管。
  toggle(doc).click();
  find(doc);
  assert.ok(await searchBar(doc));
});

test("焦点回到正文后 Esc 一样关得掉搜索框", async () => {
  const { doc, bar } = await searchPage("alpha beta alpha");
  type(bar, "alpha");
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 2);

  // 用户点过正文，焦点已经不在搜索框里了。
  const view = doc.defaultView as Window & typeof globalThis;
  doc.dispatchEvent(new view.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  assert.equal(doc.querySelector(".lfv-search"), null);
  assert.equal(doc.querySelectorAll(".lfv-hit").length, 0);
});

/** 造一张渲染好的 markdown 文件页，并把存储里的设置预置成 `stored`。 */
async function themedPage(stored: Record<string, unknown> = {}) {
  const store = storageMock({ "viewer-settings": stored });
  const dom = textFile("file:///tmp/docs/guide.md", "");
  const doc = dom.window.document;
  first(doc).textContent = "# 标题\n\n一段正文。\n";
  prettify(doc);
  await rendered(doc);
  // 主题是异步读存储之后才写上去的，等这一拍。
  await new Promise((resolve) => setTimeout(resolve, 10));
  return { dom, doc, store };
}

test("没设置过时用默认配色「深潭」和默认风格「文稿」", async () => {
  const { doc } = await themedPage();

  const root = doc.documentElement;
  // 颜色不在 JS 里算：只写色相角和彩度系数两个数，其余十几个颜色由 CSS 的 oklch 公式推。
  assert.equal(root.style.getPropertyValue("--lfv-h"), "200");
  assert.equal(root.style.getPropertyValue("--lfv-c"), "1");
  assert.equal(root.dataset["lfvPalette"], "pool");
  assert.equal(root.dataset["lfvPolarity"], "dark");
  assert.equal(root.dataset["lfvStyle"], "manuscript");
  assert.equal(root.style.colorScheme, "dark");
});

test("配色和风格互相独立，换一个不动另一个", async () => {
  const { doc } = await themedPage({ theme: "ochre", style: "console" });

  const root = doc.documentElement;
  assert.equal(root.style.getPropertyValue("--lfv-h"), "40");
  assert.equal(root.style.getPropertyValue("--lfv-c"), "1.15");
  assert.equal(root.dataset["lfvPolarity"], "light");
  assert.equal(root.dataset["lfvStyle"], "console");
});

test("墨水屏的彩度是 0——纯灰阶是同一条公式的边界值，不是另一张表", async () => {
  const { doc } = await themedPage({ theme: "eink" });

  assert.equal(doc.documentElement.style.getPropertyValue("--lfv-c"), "0");
  assert.equal(doc.documentElement.dataset["lfvPalette"], "eink");
});

test("自定义只盖住改过的那几个值，没改的仍由公式算", async () => {
  const { doc } = await themedPage({
    theme: "custom",
    custom: { base: "brass", patch: { background: "#101010", "syn-keyword": "#ff0000" } },
  });

  const root = doc.documentElement;
  // 底子仍然是黄铜，所以色相还是 85；只有改过的两个值被行内变量盖掉。
  assert.equal(root.style.getPropertyValue("--lfv-h"), "85");
  assert.equal(root.style.getPropertyValue("--background"), "#101010");
  assert.equal(root.style.getPropertyValue("--syn-keyword"), "#ff0000");
  // 没改过的不写行内值，留给 CSS 公式。
  assert.equal(root.style.getPropertyValue("--foreground"), "");
});

test("别处改了主题，这一页跟着换——不用刷新", async () => {
  const { doc } = await themedPage();
  assert.equal(doc.documentElement.dataset["lfvPalette"], "pool");

  // 模拟另一个标签页把设置改了：`chrome.storage` 的变更事件是跨标签页广播的。
  await (chrome.storage.local as unknown as { set: (items: object) => Promise<void> }).set({
    "viewer-settings": { theme: "einkd", style: "brief" },
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal(doc.documentElement.dataset["lfvPalette"], "einkd");
  assert.equal(doc.documentElement.dataset["lfvStyle"], "brief");
});

test("主题按钮点开是两段：八套配色加自定义、四种风格", async () => {
  const { dom, doc, store } = await themedPage();

  const button = doc.querySelector(".lfv-theme-toggle") as HTMLElement;
  assert.equal(button.textContent, "主题");
  assert.equal(button.classList.contains("lfv-toggle"), false);

  button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 10));

  const menu = doc.getElementById("lfv-theme-menu") as HTMLElement;
  const palettes = [...menu.querySelectorAll("button[data-palette]")] as HTMLElement[];
  const styles = [...menu.querySelectorAll("button[data-style]")] as HTMLElement[];
  assert.deepEqual(
    palettes.map((item) => item.dataset["palette"]),
    ["brass", "ochre", "mauve", "eink", "pool", "soot", "night", "einkd", "custom"],
  );
  assert.deepEqual(
    styles.map((item) => item.dataset["style"]),
    ["manuscript", "press", "console", "brief"],
  );
  // 当前那套后面带一个点，不用另开一列图标。
  assert.equal(palettes[4]?.textContent, "深潭 ·");

  styles[1]?.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal((store.data["viewer-settings"] as { style: string }).style, "press");
  assert.equal(doc.getElementById("lfv-theme-menu"), null);
});

test("切回原始时主题按钮收起来，切回美化又回来", async () => {
  const { doc } = await themedPage();
  assert.ok(doc.querySelector(".lfv-theme-toggle"));

  (doc.querySelector(".lfv-toggle") as HTMLElement).click();
  assert.equal(doc.querySelector(".lfv-theme-toggle"), null);

  (doc.querySelector(".lfv-toggle") as HTMLElement).click();
  assert.ok(doc.querySelector(".lfv-theme-toggle"));
});

// ------------------------------------------------ 懒加载面与构建入口一致性

test("LAZY 的每个包都有对应的构建入口，忘了加 entry 就过不了测试", async () => {
  // @ts-expect-error build.mjs 是构建脚本，没有类型声明；这里只借它的 entryPoints 清单
  const { options } = await import("../build.mjs");
  const built = options.entryPoints.map((entry: string) => entry.replace(/^src\/|.ts$/g, ""));
  for (const key of Object.keys(LAZY)) {
    assert.ok(built.includes(key), `LAZY.${key} 想要 ${key}.js，但构建入口里没有 src/${key}.ts`);
  }
  // 反向：构建出的懒加载包要是没人加载，多半是改漏了 LAZY
  const lazyBuilt = built.filter((name: string) =>
    !["background", "settings-page", "viewer-page"].includes(name));
  for (const name of lazyBuilt) {
    assert.ok(name in LAZY, `src/${name}.ts 构建成了独立包，但 LAZY 里没有它的加载点`);
  }
});
