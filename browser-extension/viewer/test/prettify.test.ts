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
        // 懒加载的两个包在浏览器里是 dist 产物，测试里直接指回源码，让 `import()` 真的能加载。
        if (path === "highlight.js") return new URL("../src/highlight.ts", import.meta.url).href;
        if (path === "markdown.js") return new URL("../src/markdown.ts", import.meta.url).href;
        // 画图和公式那两个包在 node 里跑不起来（一个要真的排版，一个 import 了 CSS），换成桩。
        if (path === "mermaid.js") return new URL("./stub-mermaid.ts", import.meta.url).href;
        if (path === "katex.js") return new URL("./stub-katex.ts", import.meta.url).href;
        if (path === "data.js") return new URL("../src/data.ts", import.meta.url).href;
        if (path === "csv.js") return new URL("../src/csv.ts", import.meta.url).href;
        return `chrome-extension://viewer/${path}`;
      },
    },
  });
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
 * 造一张带链接的 markdown 页，等它渲染完，并把两件跟外界打交道的事换成可观察的桩：
 * `fetch`（探文件在不在）和 `window.open`（跳转）。`existing` 里列的绝对地址算「文件存在」。
 */
async function linkPage(text: string, existing: string[] = []) {
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
    return { ok: existing.includes(url) } as Response;
  };
  // 没被拦下的链接会走 jsdom 的默认导航（它没实现，只会往控制台吐一行），这里统一收掉。
  doc.addEventListener("click", (event) => event.preventDefault());

  prettify(doc);
  const host = await rendered(doc);
  const click = (href: string) =>
    host
      .querySelector(`a[href="${href}"]`)
      ?.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  return { doc, host, opened, probed, click };
}

test("同级、子目录、上层的相对链接都解析对，文件在就在当前标签页翻过去", async () => {
  const { opened, probed, click } = await linkPage(
    "[同级](./api.md)\n\n[子目录](guide/x.md)\n\n[上层](../readme.md)\n",
    ["file:///tmp/docs/api.md", "file:///tmp/docs/guide/x.md", "file:///tmp/readme.md"],
  );

  click("./api.md");
  click("guide/x.md");
  click("../readme.md");
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(probed, [
    "file:///tmp/docs/api.md",
    "file:///tmp/docs/guide/x.md",
    "file:///tmp/readme.md",
  ]);
  // `_self` = 当前标签页导航并留下一条历史，前进后退因此照常可用；
  // 新页面还是 `file://`，内容脚本照样接管，所以退回来看到的仍是渲染好的样子。
  assert.deepEqual(opened, [
    "file:///tmp/docs/api.md _self",
    "file:///tmp/docs/guide/x.md _self",
    "file:///tmp/readme.md _self",
  ]);
});

test("链接目标不存在时当场说找不到，不跳过去", async () => {
  const { host, opened, click } = await linkPage("[没了](./gone.md)\n");

  click("./gone.md");
  await new Promise((resolve) => setTimeout(resolve, 20));

  const note = host.querySelector(".lfv-missing");
  assert.equal(note?.textContent?.includes("找不到这个文件：gone.md"), true);
  assert.deepEqual(opened, []);

  // 点两次只留一条提示。
  click("./gone.md");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(host.querySelectorAll(".lfv-missing").length, 1);

  // 「仍然打开」是用户明确要去，不再拦第二遍。
  (host.querySelector(".lfv-anyway") as HTMLElement).dispatchEvent(
    new (host.ownerDocument.defaultView as unknown as { MouseEvent: typeof MouseEvent }).MouseEvent(
      "click",
      { bubbles: true, cancelable: true },
    ),
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(opened, []);
});

test("图片、压缩包这类链接交回浏览器，不拦", async () => {
  const { opened, probed, click } = await linkPage("[图](./a.png)\n\n[包](./b.zip)\n");

  click("./a.png");
  click("./b.zip");
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(probed, []);
  assert.deepEqual(opened, []);
});

test("网络地址的链接照常打开，不被当成本地文件", async () => {
  const { opened, probed, click } = await linkPage("[网页](https://example.test/a.md)\n");

  click("https://example.test/a.md");
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(probed, []);
  assert.deepEqual(opened, []);
});

test("文档内的锚点跳转不走本地文件那条路", async () => {
  const { opened, probed, click } = await linkPage("# 安装\n\n[回到安装](#安装)\n");

  click("#安装");
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(probed, []);
  assert.deepEqual(opened, []);
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
