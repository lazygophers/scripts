import assert from "node:assert/strict";
import test from "node:test";

import { renderMarkdown } from "../src/markdown.ts";
import { page } from "./mock.ts";

/**
 * markdown 各路方言。每一种都是一个 marked 扩展，认不出来就当普通文本走，
 * 所以「写错的语法不会把整篇文档带崩」这条也要测到。
 *
 * 这里从 renderMarkdown 进（而不是直接调扩展），测的就是用户真看到的 HTML——
 * 净化那一关也一并走过，扩展生成的标签被 DOMPurify 摘掉的话这里立刻现形。
 */

function html(text: string): string {
  const dom = page("");
  return renderMarkdown(dom.window.document, text).innerHTML;
}

function doc(text: string) {
  const dom = page("");
  return renderMarkdown(dom.window.document, text);
}

test("双方括号链接：目标没写扩展名就补 .md", () => {
  const link = doc("[[设计笔记]]\n").querySelector("a.lfv-wikilink");
  // href 走 encodeURI，中文文件名是百分号转义的形态
  assert.equal(link?.getAttribute("href"), encodeURI("设计笔记.md"));
  assert.equal(link?.textContent, "设计笔记");
});

test("双方括号链接：写了扩展名就原样用", () => {
  assert.equal(
    doc("[[图.png]]\n").querySelector("a.lfv-wikilink")?.getAttribute("href"),
    encodeURI("图.png"),
  );
});

test("双方括号链接：竖线后面是显示文字", () => {
  const link = doc("[[页面|看这里]]\n").querySelector("a.lfv-wikilink");
  assert.equal(link?.getAttribute("href"), encodeURI("页面.md"));
  assert.equal(link?.textContent, "看这里");
});

test("双方括号链接：显示文字里的 HTML 被转义，不生成标签", () => {
  const article = doc("[[页面|<b>粗</b>]]\n");
  assert.equal(article.querySelector("a.lfv-wikilink b"), null);
  assert.equal(article.querySelector("a.lfv-wikilink")?.textContent, "<b>粗</b>");
});

test("上标和下标", () => {
  const article = doc("x^2^ 和 H~2~O\n");
  assert.equal(article.querySelector("sup")?.textContent, "2");
  assert.equal(article.querySelector("sub")?.textContent, "2");
});

test("两个波浪线仍是删除线，不被下标抢走", () => {
  const article = doc("~~删掉~~\n");
  assert.equal(article.querySelector("del")?.textContent, "删掉");
  assert.equal(article.querySelector("sub"), null);
});

test("定义列表：一行术语加若干条冒号解释", () => {
  const article = doc("术语\n: 第一条解释\n: 第二条解释\n");
  assert.equal(article.querySelector("dl dt")?.textContent, "术语");
  assert.deepEqual(
    Array.from(article.querySelectorAll("dl dd"), (dd) => dd.textContent),
    ["第一条解释", "第二条解释"],
  );
});

test("提示框：表里的类型各有自己的色条类名", () => {
  for (const kind of ["note", "tip", "info", "warning", "caution", "danger"]) {
    const article = doc(`:::${kind}\n小心\n:::\n`);
    assert.equal(article.querySelector(".lfv-note")?.className, `lfv-note lfv-note-${kind}`, kind);
  }
});

test("提示框：表里没有的类型按 note 处理", () => {
  assert.equal(
    doc(":::赶紧看\n内容\n:::\n").querySelector(".lfv-note")?.className,
    "lfv-note lfv-note-note",
  );
});

test("提示框里的 markdown 照常解析", () => {
  assert.equal(doc(":::tip\n**重点**\n:::\n").querySelector(".lfv-note strong")?.textContent, "重点");
});

test("行内公式只挑出原文放进 data-tex，排版留给 KaTeX", () => {
  const node = doc("质能方程 $E=mc^2$ 如上\n").querySelector("span.lfv-math");
  assert.equal(node?.getAttribute("data-tex"), "E=mc^2");
  assert.equal(node?.textContent, "$E=mc^2$", "KaTeX 没加载上时看到的是原文，不是空白");
});

test("独立成行的公式用 block 类名", () => {
  const node = doc("$$\n\\int_0^1 x\\,dx\n$$\n").querySelector("div.lfv-math-block");
  assert.equal(node?.getAttribute("data-tex"), "\\int_0^1 x\\,dx");
});

test("美元后面紧跟空格的不算公式", () => {
  const article = doc("价格 $ 5 起，另一处 $ 9\n");
  assert.equal(article.querySelector(".lfv-math"), null);
});

test("脚注渲染成引用和文末列表", () => {
  const article = doc("正文[^1]\n\n[^1]: 脚注内容\n");
  assert.ok(article.querySelector("sup a, a[href^='#footnote']"), "正文里要有引用链接");
  assert.match(article.textContent ?? "", /脚注内容/);
});

test("写错的方言语法不把文档带崩，按普通文本走", () => {
  const article = doc("[[没闭合\n\n:::\n\n^孤零零\n\n正文还在\n");
  assert.match(article.textContent ?? "", /正文还在/);
  assert.match(article.textContent ?? "", /没闭合/);
});

test("扩展生成的标签能过净化这一关", () => {
  // 双方括号链接 / 提示框 / 公式各生成一种自定义 class，被 DOMPurify 摘掉就说明配置错了
  const out = html("[[页面]]\n\n:::tip\n提示\n:::\n\n$x$\n");
  assert.match(out, /lfv-wikilink/);
  assert.match(out, /lfv-note/);
  assert.match(out, /lfv-math/);
});
