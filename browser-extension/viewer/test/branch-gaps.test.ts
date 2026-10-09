import assert from "node:assert/strict";
import test from "node:test";

import { Marked } from "marked";

import { parseCsv, renderTable } from "../src/csv.ts";
import { parseData, renderTree } from "../src/data.ts";
import { parseLine, renderLog } from "../src/log.ts";
import { openSearch } from "../src/search.ts";
import { renderMarkdown, renderToc } from "../src/markdown.ts";
import { parseListing, renderListing } from "../src/listing.ts";
import { DIALECTS } from "../src/dialects.ts";
import { openDiagramPreview, closeDiagramPreview } from "../src/diagram-preview.ts";
import { applyTheme, contrast, hexOf } from "../src/themes.ts";
import { page } from "./mock.ts";

/* ---------- csv / prettify 的补充 ---------- */

import { show } from "../src/prettify.ts";
import { installChrome, storageMock, clearChrome } from "./mock.ts";

test("整列全空的列排序：空格之间持平", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["s"], [""], [""]]);
  const th = table.querySelectorAll("th")[0] as HTMLElement;
  (th.querySelector(".lfv-th-label") as HTMLElement).dispatchEvent(
    new dom.window.MouseEvent("click", { bubbles: true }),
  );
  const values = Array.from(table.querySelectorAll("tbody td"), (td) => td.textContent);
  assert.deepEqual(values, ["", ""]);
});

const realFetch = globalThis.fetch;

test("展示页本地链接：修饰键、右键、被preventDefault、无href、页内锚点都交回浏览器", async () => {
  const sent: unknown[] = [];
  installChrome({
    runtime: {
      getURL: (path: string) => new URL(`../src/${path.replace(/\.js$/, ".ts")}`, import.meta.url).href,
      sendMessage: async (message: unknown) => { sent.push(message); },
    },
  });
  storageMock();
  const dom = page("", { url: "chrome-extension://viewer/page?file=a.md" });
  const doc = dom.window.document;
  show(doc, "chrome-extension://viewer/a.md", "正文");
  for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r));

  const host = doc.querySelector("article") as HTMLElement;
  const link = doc.createElement("a");
  link.href = "file:///b.txt";
  link.textContent = "b";
  host.append(link);
  const click = (init: MouseEventInit) =>
    link.dispatchEvent(new dom.window.MouseEvent("click", init));

  click({ button: 2 });
  click({ button: 1 });
  click({ button: 0, metaKey: true });
  click({ button: 0, ctrlKey: true });
  assert.deepEqual(sent, [], "以上四种都不该交给后台脚本");
  // 被 preventDefault 过的交给浏览器：dispatchEvent 不会自动带上，手动拦一次。
  const prevented = new dom.window.MouseEvent("click", { button: 0, cancelable: true, bubbles: true });
  link.dispatchEvent(prevented);
  assert.equal(prevented.defaultPrevented, true, "普通左键要拦下来交给后台");
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(sent, [{ type: "lfv-open", url: "file:///b.txt" }]);

  // 无 href 的 <a> 和页内锚点不拦。
  const bare = doc.createElement("a");
  bare.textContent = "bare";
  host.append(bare);
  bare.dispatchEvent(new dom.window.MouseEvent("click", { button: 0, bubbles: true }));
  const hash = doc.createElement("a");
  hash.href = "#section";
  host.append(hash);
  hash.dispatchEvent(new dom.window.MouseEvent("click", { button: 0, bubbles: true }));
  await new Promise((r) => setImmediate(r));
  assert.equal(sent.length, 1, "多出来的点击都不产生消息");
  clearChrome();
  globalThis.fetch = realFetch;
});

test("主题菜单：点开、点按钮收起、点菜单项落盘并收起；Ctrl+F 开查找，别的键和裸 f 不开", async () => {
  installChrome({
    runtime: {
      getURL: (path: string) => new URL(`../src/${path.replace(/\.js$/, ".ts")}`, import.meta.url).href,
    },
  });
  storageMock({ "viewer-settings": { theme: "pool", style: "manuscript", custom: { base: "pool", patch: {} } } });
  const dom = page("");
  const doc = dom.window.document;
  show(doc, "file:///a.md", "# 标题\n\n正文");
  const tick = () => new Promise((r) => setImmediate(r));

  // 主题按钮在美化档上，点开浮出菜单。
  const themeButton = doc.querySelector(".lfv-theme-toggle") as HTMLElement;
  assert.ok(themeButton);
  themeButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await tick();
  await tick();
  const menu = doc.getElementById("lfv-theme-menu");
  assert.ok(menu, "点开应有菜单");

  // 再点按钮：收起（不靠点别处）。
  themeButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.getElementById("lfv-theme-menu"), null);

  // 再开，点一个配色行：写存储、菜单收起。
  themeButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await tick();
  await tick();
  const row = doc.querySelector("#lfv-theme-menu [data-palette]") as HTMLElement;
  row.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await tick();
  await tick();
  assert.equal(doc.getElementById("lfv-theme-menu"), null);

  // 查找快捷键：只在美化档接管；别的键、裸 f 都不开。
  const press = (init: KeyboardEventInit) =>
    doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", init));
  press({ key: "x" });
  press({ key: "f" });
  assert.equal(doc.querySelector(".lfv-search"), null, "裸 f 不该开查找");
  press({ key: "f", ctrlKey: true });
  await tick();
  await tick();
  assert.ok(doc.querySelector(".lfv-search"), "Ctrl+F 应开查找");
  clearChrome();
  globalThis.fetch = realFetch;
});

test("裸 CR 也是行分隔，后面不跟 LF", () => {
  assert.deepEqual(parseCsv("a,b\rc,d"), [["a", "b"], ["c", "d"]]);
});

test("引号字段后面跟裸 CR", () => {
  assert.deepEqual(parseCsv('"x"\r"y"'), [["x"], ["y"]]);
});

test("排序时空格子互相比较持平，且空格与非空格两个方向都排得出", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["s"], [""], [""], ["a"], ["b"]]);
  const th = table.querySelectorAll("th")[0] as HTMLElement;
  (th.querySelector(".lfv-th-label") as HTMLElement).dispatchEvent(
    new dom.window.MouseEvent("click", { bubbles: true }),
  );
  const values = Array.from(table.querySelectorAll("tbody td"), (td) => td.textContent);
  assert.deepEqual(values, ["a", "b", "", ""]);
});

test("点到 thead 本身（不是表头格）不排序", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["2"], ["1"]]);
  (table.querySelector("thead") as HTMLElement).dispatchEvent(
    new dom.window.MouseEvent("click", { bubbles: true }),
  );
  assert.equal(table.querySelectorAll("th")[0]?.hasAttribute("data-sort"), false);
});

test("拖把手改列宽：按下、拖动、松开", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["1"]]);
  const grip = table.querySelector(".lfv-grip") as HTMLElement;
  const events: [EventTarget, string, MouseEventInit][] = [
    [grip, "mousedown", { bubbles: true, clientX: 10 }],
    [dom.window.document, "mousemove", { clientX: 60 }],
    [dom.window.document, "mouseup", {}],
  ];
  for (const [target, type, init] of events) {
    target.dispatchEvent(new dom.window.MouseEvent(type, init));
  }
  const th = table.querySelector("th") as HTMLElement;
  assert.equal(th.style.width, "50px", "10 + 60 - 10 = 50");
});

test("按下时目标不是把手，拖动不生效", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["1"]]);
  const label = table.querySelector(".lfv-th-label") as HTMLElement;
  label.dispatchEvent(new dom.window.MouseEvent("mousedown", { bubbles: true, clientX: 10 }));
  dom.window.document.dispatchEvent(new dom.window.MouseEvent("mousemove", { clientX: 100 }));
  const th = table.querySelector("th") as HTMLElement;
  assert.equal(th.style.width, "", "没抓到把手就不该写宽度");
});

/* ---------- data ---------- */

test("JSON 报错只给原文片段（没有省略号）时按片段位置定位", () => {
  const parsed = parseData('{"a"@}', false);
  assert.equal(parsed.ok, false);
  if (parsed.ok) return;
  assert.ok(parsed.error.line >= 1);
});

test("JSON 报错带省略号片段时按截断补偿换算", () => {
  const text = '{\n  "a": 1,\n  "b": ,\n  "c": 3,\n  "d": 4\n}\n';
  const parsed = parseData(text, false);
  assert.equal(parsed.ok, false);
  if (parsed.ok) return;
  assert.ok(parsed.error.line >= 1);
});

test("点到树的空白处不复制路径", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { a: 1 });
  dom.window.document.body.append(tree);
  tree.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(tree.querySelector(".lfv-key")?.textContent, "a: ", "没点在键上，文案不变");
});

/* ---------- log ---------- */

test("整行 JSON 没有级别和时间字段时按无级别显示", () => {
  const line = parseLine('{"msg":"hi"}');
  assert.equal(line.level, null);
  assert.equal(line.time, "");
  assert.equal(line.json !== undefined, true);
});

test("不认得的级别词归为无级别，级别大小写归一", () => {
  assert.equal(parseLine("2026-01-01 00:00:00 WARNING be careful").level, "warn");
  assert.equal(parseLine("2026-01-01 00:00:00 LOUD bang").level, null);
});

test("一行 JSON 都没有时不加载折叠树", async () => {
  const dom = page("");
  const seen: string[] = [];
  const { installChrome } = await import("./mock.ts");
  installChrome({ runtime: { getURL: (p: string) => { seen.push(p); return `x://${p}`; } } });
  const host = await renderLog(dom.window.document, "plain line\nanother\n");
  assert.deepEqual(seen, [], "没有 JSON 就不该去取 data.js");
  assert.equal(host.querySelectorAll(".lfv-log-row").length, 2);
});

/* ---------- search ---------- */

test("style 里的正文不参与搜索，命中处滚动到眼前", () => {
  const dom = page('<style>.needle { color: red }</style><p>needle here</p>');
  const scrolled: unknown[] = [];
  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollIntoView", {
    value: function (this: HTMLElement) { scrolled.push(this); },
    configurable: true,
  });
  const bar = openSearch(dom.window.document);
  const input = bar.querySelector("input") as HTMLInputElement;
  input.value = "needle";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  const marks = dom.window.document.querySelectorAll("mark");
  assert.equal(marks.length, 1, "style 里的那个不算");
  assert.equal(scrolled.length, 1);
});

/* ---------- markdown ---------- */

test("front matter 渲染成信息表，没有冒号的行跳过", () => {
  const dom = page("");
  const article = renderMarkdown(dom.window.document, "---\ntitle: Hello\nbroken line\n---\n\n正文\n");
  const table = article.querySelector("table.lfv-front-matter");
  assert.ok(table);
  assert.match(table?.textContent ?? "", /title/);
  assert.match(table?.textContent ?? "", /Hello/);
  assert.doesNotMatch(table?.textContent ?? "", /broken/);
});

test("没有 front matter 就没有信息表", () => {
  const dom = page("");
  const article = renderMarkdown(dom.window.document, "正文\n");
  assert.equal(article.querySelector("table.lfv-front-matter"), null);
});

test("slug 全被剥光时退到 section", () => {
  const dom = page("");
  const article = renderMarkdown(dom.window.document, "### !!!\n");
  assert.equal(article.querySelector("h3")?.id, "section");
});

test("mdx: true 时 import/export 行删掉、组件留占位", () => {
  const dom = page("");
  const article = renderMarkdown(
    dom.window.document,
    'import X from "x"\n\n# 标题\n\n<X a={1} />\n',
    true,
  );
  assert.doesNotMatch(article.textContent ?? "", /import/);
  assert.ok(article.querySelector(".lfv-mdx"));
});

/* ---------- listing ---------- */

function listingDom() {
  return page('<table id="tbody"><tbody></tbody></table>');
}

test("缺格子的行也能读：大小和时间为空", async () => {
  const dom = listingDom();
  const doc = dom.window.document;
  const tbody = doc.querySelector("tbody") as HTMLElement;
  const tr = doc.createElement("tr");
  const td = doc.createElement("td");
  const a = doc.createElement("a");
  a.textContent = "README.md/";
  td.append(a);
  tr.append(td);
  tbody.append(tr);
  const entries = parseListing(tbody);
  assert.deepEqual(entries, [
    {
      name: "README.md",
      url: "",
      dir: false,
      size: 0,
      sizeText: "",
      mtime: 0,
      mtimeText: "",
    },
  ]);
});

test("预览：读第一块就掐断连接", async () => {
  const dom = page("");
  let cancelled = 0;
  const chunk = new TextEncoder().encode("l1\nl2\nl3\nl4\nl5\nl6\nl7\n");
  globalThis.fetch = (async () => ({
    body: {
      getReader: () => ({
        read: async () => ({ done: false, value: chunk }),
        cancel: async () => { cancelled += 1; },
      }),
    },
  })) as unknown as typeof fetch;

  const host = renderListing(
    dom.window.document,
    [{ name: "a.txt", url: "file:///a.txt", dir: false, size: 1, sizeText: "1", mtime: 1, mtimeText: "昨天" }],
    "/",
  );
  const link = host.querySelector("a.lfv-entry") as HTMLElement;
  link.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  await new Promise((r) => setImmediate(r));
  const box = host.querySelector(".lfv-preview pre") as HTMLElement;
  assert.equal(box?.textContent?.split("\n").length, 5, "只留 5 行");
  assert.equal(cancelled, 1, "读完第一块就 cancel");
});

test("预览：读不出时把原因写在原地", async () => {
  const dom = page("");
  globalThis.fetch = (async () => {
    throw new Error("EACCES");
  }) as unknown as typeof fetch;
  const host = renderListing(
    dom.window.document,
    [{ name: "a.txt", url: "file:///a.txt", dir: false, size: 1, sizeText: "1", mtime: 1, mtimeText: "昨天" }],
    "/",
  );
  const link = host.querySelector("a.lfv-entry") as HTMLElement;
  link.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  await new Promise((r) => setImmediate(r));
  assert.match((host.querySelector(".lfv-preview pre") as HTMLElement).textContent ?? "", /读不出这个文件：EACCES/);
});

test("预览框移开隐藏、回来直接显示不再读", async () => {
  const dom = page("");
  let reads = 0;
  globalThis.fetch = (async () => ({ text: async () => { reads += 1; return "hi"; } })) as unknown as typeof fetch;
  const host = renderListing(
    dom.window.document,
    [{ name: "a.txt", url: "file:///a.txt", dir: false, size: 1, sizeText: "1", mtime: 1, mtimeText: "t" }],
    "/",
  );
  const link = host.querySelector("a.lfv-entry") as HTMLElement;
  link.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  await new Promise((r) => setImmediate(r));
  const box = host.querySelector(".lfv-preview") as HTMLElement;
  link.dispatchEvent(new dom.window.MouseEvent("mouseleave"));
  assert.equal(box.hidden, true);
  link.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  assert.equal(box.hidden, false);
  assert.equal(reads, 1, "第二次进来不该重读");
});

/* ---------- dialects ---------- */

const md = () => new Marked(...DIALECTS);

test("wikilink：带扩展名的目标不补 .md，别名照显示", () => {
  const withExt = md().parse("[[page.pdf]]") as string;
  assert.match(withExt, /href="page\.pdf"/);
  const noExt = md().parse("[[page]]") as string;
  assert.match(noExt, /href="page\.md"/);
  const alias = md().parse("[[page|别名]]") as string;
  assert.match(alias, />别名</);
});

test("上下标渲染成 sup / sub，HTML 字符被转义", () => {
  const html = md().parse("^x^ 和 ~y~") as string;
  assert.match(html, /<sup>x<\/sup>/);
  assert.match(html, /<sub>y<\/sub>/);
});

test("定义列表：术语加若干条解释", () => {
  const html = md().parse("术语\n: 解释一\n: 解释二\n") as string;
  assert.match(html, /<dt>术语<\/dt>/);
  assert.equal((html.match(/<dd>/g) ?? []).length, 2);
});

test("提示框：认得的名字用自己那档，不认得的归 note", () => {
  const danger = md().parse(":::danger\n小心\n:::\n") as string;
  assert.match(danger, /lfv-note-danger/);
  const weird = md().parse(":::weird\n字\n:::\n") as string;
  assert.match(weird, /lfv-note-note/);
});

test("数学：块级和行内都挑出 data-tex", () => {
  const html = md().parse("行内 $a^2$ 与块级：\n\n$$b_2$$\n") as string;
  assert.match(html, /data-tex="a\^2"/);
  assert.match(html, /data-tex="b_2"/);
});

/* ---------- diagram-preview ---------- */

function geoDom() {
  const dom = page('<div class="stage-host"></div>');
  const proto = dom.window.HTMLElement.prototype as unknown as Record<string, unknown>;
  proto.getBoundingClientRect = function () {
    return { width: 400, height: 200, left: 0, top: 0, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) };
  };
  Object.defineProperty(proto, "clientWidth", { get: () => 200, configurable: true });
  Object.defineProperty(proto, "clientHeight", { get: () => 100, configurable: true });
  return dom;
}

test("预览：初始按视口适配（50%），按钮缩放、重置、关闭", () => {
  const dom = geoDom();
  const doc = dom.window.document;
  openDiagramPreview(doc, '<svg width="10" height="10"></svg>');
  const bar = doc.querySelector(".lfv-lightbox-bar") as HTMLElement;
  const readout = () => (doc.querySelector(".lfv-lightbox-zoom") as HTMLElement).textContent;
  assert.equal(readout(), "50%", "200/400 和 100/200 里取小，封顶 100%");

  const buttons = Array.from(bar.querySelectorAll("button")) as HTMLElement[];
  const [zoomIn, zoomOut, reset, close] = [buttons[0]!, buttons[1]!, buttons[2]!, buttons[3]!];
  zoomIn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(readout(), "60%");
  zoomOut.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(readout(), "50%");
  const stage = doc.querySelector(".lfv-lightbox-stage") as HTMLElement;
  stage.dispatchEvent(
    new dom.window.MouseEvent("dblclick", { bubbles: true, clientX: 100, clientY: 50 }),
  );
  assert.equal(readout(), "60%");
  reset.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(readout(), "50%");
  close.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.querySelector(".lfv-lightbox"), null);
});

test("预览：滚轮缩放两个方向、拖动、非左键和未按下不拖", () => {
  const dom = geoDom();
  const doc = dom.window.document;
  openDiagramPreview(doc, "<svg></svg>");
  const stage = doc.querySelector(".lfv-lightbox-stage") as HTMLElement;
  const readout = () => (doc.querySelector(".lfv-lightbox-zoom") as HTMLElement).textContent;

  const wheel = (deltaY: number) => {
    const ev = new dom.window.MouseEvent("wheel", { cancelable: true });
    Object.defineProperty(ev, "deltaY", { value: deltaY });
    stage.dispatchEvent(ev);
  };
  wheel(-100);
  assert.equal(readout(), "60%");
  wheel(100);
  assert.equal(readout(), "50%");

  // 没按下就移动：不该拖。
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove"));
  const before = (doc.querySelector(".lfv-lightbox-inner") as HTMLElement).style.transform;
  // 右键按下：不开始拖。
  stage.dispatchEvent(new dom.window.MouseEvent("pointerdown", { button: 2 }));
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove"));
  assert.equal((doc.querySelector(".lfv-lightbox-inner") as HTMLElement).style.transform, before);

  stage.setPointerCapture = () => {};
  stage.releasePointerCapture = () => {};
  stage.dispatchEvent(new dom.window.MouseEvent("pointerdown", { button: 0, clientX: 10, clientY: 10 }));
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 40, clientY: 30 }));
  assert.match(
    (doc.querySelector(".lfv-lightbox-inner") as HTMLElement).style.transform,
    /translate\(130px, 70px\)/,
    "起点 (100, 50) 加位移 (30, 30)——初始位置是 centerAt 摆出来的",
  );
  stage.dispatchEvent(new dom.window.MouseEvent("pointerup"));
});

test("预览：Esc、点背景都关；再开一次先清旧的", () => {
  const dom = geoDom();
  const doc = dom.window.document;
  openDiagramPreview(doc, "<svg></svg>");
  const backdrop = doc.querySelector(".lfv-lightbox") as HTMLElement;
  doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(doc.querySelector(".lfv-lightbox"), null);

  openDiagramPreview(doc, "<svg></svg>");
  openDiagramPreview(doc, "<svg></svg>");
  assert.equal(doc.querySelectorAll(".lfv-lightbox").length, 1, "同时只开一个");
  const second = doc.querySelector(".lfv-lightbox") as HTMLElement;
  second.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.querySelector(".lfv-lightbox"), null);
  assert.ok(backdrop);
});

test("closeDiagramPreview 没开着的预览时不炸", () => {
  const dom = page("");
  closeDiagramPreview(dom.window.document);
  assert.ok(true);
});

/* ---------- themes ---------- */

test("applyTheme：认不得的配色退到 pool，认不得的风格退到 manuscript", () => {
  const dom = page("");
  const applied = applyTheme(dom.window.document, "nope", "also-nope");
  assert.equal(applied.palette.id, "pool");
  assert.equal(applied.style.id, "manuscript");
});

test("applyTheme：自定义模式盖指定的变量", () => {
  const dom = page("");
  const applied = applyTheme(dom.window.document, "custom", "manuscript", {
    base: "pool",
    patch: { background: "#101010" },
  });
  assert.equal(applied.custom, true);
  assert.equal(
    dom.window.document.documentElement.style.getPropertyValue("--background"),
    "#101010",
  );
  // 没盖到的变量要被摘掉，让公式算。
  assert.equal(dom.window.document.documentElement.style.getPropertyValue("--accent"), "");
});

test("contrast：黑对白是 21:1，同色是 1:1", () => {
  assert.equal(contrast("#000000", "#ffffff").toFixed(2), "21.00");
  assert.equal(contrast("#808080", "#808080").toFixed(2), "1.00");
});

test("hexOf：jsdom 里算不出画布时退回原值；rgb() 写法转成 #rrggbb", () => {
  const dom = page("");
  // jsdom 的 getComputedStyle 会把 style.color 原样吐回来（不解析颜色），
  // 所以 rgb() 输入走 rgbHex 那条兜底，oklch() 输入原样返回。
  assert.equal(hexOf(dom.window.document, "rgb(1, 2, 3)"), "#010203");
  assert.equal(hexOf(dom.window.document, "rgb(10, 20, 30)"), "#0a141e");
  // jsdom 不认 oklch：原样吐回（转成了无 % 写法），rgbHex 抠不出数字，原值返回。
  assert.equal(hexOf(dom.window.document, "oklch(50% 0.1 200)"), "oklch(0.5 0.1 200)");
});

test("hexOf：没有 defaultView 的环境原样返回，#hex 写法归一成小写", () => {
  const bare = {
    createElement: (tag: string) =>
      tag === "canvas" ? { getContext: null } : { style: {} as Record<string, string>, remove() {} },
    body: { append() {} },
  } as unknown as Document;
  assert.equal(hexOf(bare, "#AABBCC"), "#aabbcc");
});

test("renderToc：量不到窗口宽度时目录默认收起", () => {
  const dom = page('<article><h2 id="a">标题</h2></article>');
  const doc = dom.window.document;
  const prev = (Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(doc),
    "defaultView",
  ) ?? { get: () => dom.window }) as unknown as PropertyDescriptor;
  Object.defineProperty(doc, "defaultView", { value: undefined, configurable: true });
  try {
    const toc = renderToc(doc, doc.querySelector("article") as HTMLElement);
    assert.equal((toc as HTMLDetailsElement | null)?.open, false);
  } finally {
    Object.defineProperty(doc, "defaultView", prev);
  }
});

test("hexOf：有画布时光栅化取色", () => {
  const dom = page("");
  const proto = dom.window.HTMLCanvasElement.prototype as unknown as Record<string, unknown>;
  proto.getContext = () => ({
    set fillStyle(_v: string) {},
    get fillStyle() { return ""; },
    fillRect: () => {},
    getImageData: () => ({ data: [10, 20, 30, 255] }),
  });
  assert.equal(hexOf(dom.window.document, "oklch(50% 0.1 200)"), "#0a141e");
});
