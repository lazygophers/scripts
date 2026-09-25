import assert from "node:assert/strict";
import test from "node:test";

import { degradeMdx, renderMarkdown, renderToc } from "../src/markdown.ts";
import { page } from "./mock.ts";

/**
 * markdown 渲染：净化、front matter、标题锚点、目录。
 *
 * 净化那部分是安全边界——`<script>` 和 `onerror=` 必须在插进页面之前被摘掉，
 * 而 `file:` 又必须放行（DOMPurify 默认表里没有它，不放行本地链接就点不动）。
 * 这两件事之前没有任何测试钉住。
 */

function render(text: string, mdx = false) {
  const dom = page("");
  return { dom, article: renderMarkdown(dom.window.document, text, mdx) };
}

test("普通 markdown 渲染成 article", () => {
  const { article } = render("# 标题\n\n正文一段。\n");
  assert.equal(article.tagName, "ARTICLE");
  assert.equal(article.className, "lfv-md");
  assert.equal(article.querySelector("h1")?.id, "标题");
  assert.match(article.querySelector("p")?.textContent ?? "", /正文一段/);
});

test("标题带可点锚点，锚点的 # 不算标题文字", () => {
  const { article } = render("## 安装步骤\n");
  const anchor = article.querySelector("h2 a.lfv-anchor");
  assert.equal(anchor?.getAttribute("href"), "#安装步骤");
  assert.equal(anchor?.textContent, "#");
});

test("同名标题按出现顺序加序号，锚点不撞车", () => {
  const { article } = render("# 用法\n\n# 用法\n\n# 用法\n");
  assert.deepEqual(
    Array.from(article.querySelectorAll("h1"), (h) => h.id),
    ["用法", "用法-1", "用法-2"],
  );
});

test("标题只剩标点时退回 section", () => {
  const { article } = render("# ???\n");
  assert.equal(article.querySelector("h1")?.id, "section");
});

test("标题 id 按 GitHub 的规则：小写、去标点、空格换连字符", () => {
  const { article } = render("# Hello, World!  Again\n");
  assert.equal(article.querySelector("h1")?.id, "hello-world-again");
});

test("front matter 变成信息表，且不出现在正文里", () => {
  const { article } = render("---\ntitle: 我的文档\nauthor: me\n---\n\n正文。\n");
  const rows = Array.from(article.querySelectorAll(".lfv-front-matter tr"), (tr) => [
    tr.querySelector("th")?.textContent, tr.querySelector("td")?.textContent,
  ]);
  assert.deepEqual(rows, [["title", "我的文档"], ["author", "me"]]);
  assert.ok(!(article.textContent ?? "").includes("---"));
});

test("front matter 里没有冒号的行跳过，不变成空行目", () => {
  const { article } = render("---\ntitle: x\n这行没有冒号\n---\n\n正文\n");
  assert.equal(article.querySelectorAll(".lfv-front-matter tr").length, 1);
});

test("没有 front matter 时不插信息表", () => {
  const { article } = render("正文\n");
  assert.equal(article.querySelector(".lfv-front-matter"), null);
});

test("script 标签在插进页面之前就被摘掉", () => {
  const { article } = render("正文\n\n<script>alert(1)</script>\n");
  assert.equal(article.querySelector("script"), null);
});

test("事件处理属性被摘掉", () => {
  const { article } = render('<img src="x.png" onerror="alert(1)">\n');
  assert.equal(article.querySelector("img")?.hasAttribute("onerror"), false);
});

test("javascript: 链接的 href 被摘掉", () => {
  const { article } = render("[点我](javascript:alert(1))\n");
  assert.equal(article.querySelector("a")?.hasAttribute("href"), false);
});

test("file: 链接放行——本扩展干的就是看本地文件的活", () => {
  const { article } = render("[配置](file:///etc/hosts)\n\n![图](file:///tmp/a.png)\n");
  assert.equal(article.querySelector("a")?.getAttribute("href"), "file:///etc/hosts");
  assert.equal(article.querySelector("img")?.getAttribute("src"), "file:///tmp/a.png");
});

test("围栏代码块把语言写进 class，着色交给别人", () => {
  const { article } = render("```go\npackage main\n```\n");
  assert.equal(article.querySelector("code")?.className, "language-go");
});

test("MDX 的 import / export 行被删掉", () => {
  const degraded = degradeMdx('import Chart from "./chart";\nexport const x = 1;\n\n正文\n');
  assert.equal(degraded.trim(), "正文");
});

test("MDX 组件换成写清楚的占位，自闭合和成对都认", () => {
  const degraded = degradeMdx("<Chart data={x} />\n\n<Note>\n一段\n</Note>\n");
  const blocks = degraded.match(/lfv-mdx/g) ?? [];
  assert.equal(blocks.length, 2);
  assert.match(degraded, /<code>Chart<\/code>/);
  assert.match(degraded, /<code>Note<\/code>/);
});

test("小写开头的标签是普通 HTML，不当成组件", () => {
  const text = "<div>保留</div>\n";
  assert.equal(degradeMdx(text), text);
});

test("mdx=true 时渲染走降级，占位块真的出现在文档里", () => {
  const { article } = render('import X from "./x";\n\n<X />\n\n正文\n', true);
  assert.ok((article.textContent ?? "").includes("组件在这里跑不起来"));
  assert.ok(!(article.textContent ?? "").includes("import X"));
});

test("没有标题时不生成目录，页面上不留空侧栏", () => {
  const { dom, article } = render("只有正文。\n");
  assert.equal(renderToc(dom.window.document, article), null);
});

test("目录按标题层级列出，链接指向各自的锚点", () => {
  const { dom, article } = render("# 一\n\n## 一点一\n\n### 一点一点一\n");
  const toc = renderToc(dom.window.document, article);
  const links = Array.from(toc?.querySelectorAll("a") ?? [], (a) => [
    a.getAttribute("href"), a.dataset["level"], a.textContent,
  ]);
  assert.deepEqual(links, [
    ["#一", "1", "一"],
    ["#一点一", "2", "一点一"],
    ["#一点一点一", "3", "一点一点一"],
  ]);
});

test("目录里的标题文字不带那个锚点 #", () => {
  const { dom, article } = render("# 标题\n");
  const toc = renderToc(dom.window.document, article);
  assert.equal(toc?.querySelector("a")?.textContent, "标题");
});

test("宽窗口目录默认摊开", () => {
  const { dom, article } = render("# 一\n");
  Object.defineProperty(dom.window, "innerWidth", { value: 1400, configurable: true });
  const toc = renderToc(dom.window.document, article) as HTMLDetailsElement;
  assert.equal(toc.open, true);
});

test("窄窗口目录默认收起，点开才占地方", () => {
  const { dom, article } = render("# 一\n");
  Object.defineProperty(dom.window, "innerWidth", { value: 600, configurable: true });
  const toc = renderToc(dom.window.document, article) as HTMLDetailsElement;
  assert.equal(toc.open, false);
});
