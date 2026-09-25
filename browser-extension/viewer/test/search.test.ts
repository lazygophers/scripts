import assert from "node:assert/strict";
import test from "node:test";

import { openSearch } from "../src/search.ts";
import { page } from "./mock.ts";

/**
 * 页内搜索。存在的理由就是浏览器自带的查找搜不到折叠起来的内容，所以
 * 「命中在 `<details>` 里要把那一层打开」这条必须钉住，另外关掉搜索必须把
 * 页面还原成一个字都没动过的样子。之前没有任何测试。
 */

function search(html: string, needle: string) {
  const dom = page(html);
  const doc = dom.window.document;
  const bar = openSearch(doc);
  const input = bar.querySelector("input") as HTMLInputElement;
  input.value = needle;
  input.dispatchEvent(new dom.window.Event("input"));
  return { dom, doc, bar, input, count: () => bar.querySelector(".lfv-search-count")?.textContent };
}

/** 往整张页面发一个按键（监听挂在 document 上，不是搜索框上）。 */
function press(dom: ReturnType<typeof page>, key: string, shiftKey = false): void {
  dom.window.document.dispatchEvent(
    new dom.window.KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }),
  );
}

test("命中处包成 mark，数目报在计数上", () => {
  const { doc, count } = search("<p>alpha beta alpha</p>", "alpha");
  assert.equal(doc.querySelectorAll("mark.lfv-hit").length, 2);
  assert.equal(count(), "第 1 个 / 共 2 个");
});

test("匹配不区分大小写，但标记里保留原文大小写", () => {
  const { doc } = search("<p>Alpha ALPHA</p>", "alpha");
  assert.deepEqual(
    Array.from(doc.querySelectorAll("mark.lfv-hit"), (m) => m.textContent),
    ["Alpha", "ALPHA"],
  );
});

test("命中之外的文字原样留在原地", () => {
  const { doc } = search("<p>前 alpha 后</p>", "alpha");
  assert.equal(doc.querySelector("p")?.textContent, "前 alpha 后");
});

test("第一个命中带当前标记，其余不带", () => {
  const { doc } = search("<p>a a a</p>", "a");
  const marks = doc.querySelectorAll("mark.lfv-hit");
  assert.equal(marks[0]?.classList.contains("lfv-hit-current"), true);
  assert.equal(marks[1]?.classList.contains("lfv-hit-current"), false);
});

test("找不到时说找不到，空词什么都不说", () => {
  const { count, input, dom } = search("<p>alpha</p>", "zzz");
  assert.equal(count(), "没有找到");
  input.value = "";
  input.dispatchEvent(new dom.window.Event("input"));
  assert.equal(count(), "");
});

test("搜索框自己的文字不被搜进去", () => {
  const { doc } = search("<p>在这个文件里找 x</p>", "在这个文件里找");
  assert.equal(doc.querySelectorAll("mark.lfv-hit").length, 1, "只该命中正文那一处");
});

test("命中落在折叠节点里就把沿途每一层打开", () => {
  const { doc } = search(
    "<details><summary>外</summary><details><summary>内</summary><p>alpha</p></details></details>",
    "alpha",
  );
  const boxes = Array.from(doc.querySelectorAll("details")) as HTMLDetailsElement[];
  assert.deepEqual(boxes.map((d) => d.open), [true, true]);
});

test("回车跳下一个，最后一个再跳回到第一个", () => {
  const { dom, doc, count } = search("<p>a a</p>", "a");
  press(dom, "Enter");
  assert.equal(count(), "第 2 个 / 共 2 个");
  press(dom, "Enter");
  assert.equal(count(), "第 1 个 / 共 2 个");
  assert.equal(doc.querySelectorAll("mark.lfv-hit-current").length, 1, "当前标记只有一个");
});

test("上箭头和 shift+回车往回跳", () => {
  const { dom, count } = search("<p>a a a</p>", "a");
  press(dom, "ArrowUp");
  assert.equal(count(), "第 3 个 / 共 3 个");
  press(dom, "Enter", true);
  assert.equal(count(), "第 2 个 / 共 3 个");
});

test("没有命中时按回车不报错也不动", () => {
  const { dom, count } = search("<p>alpha</p>", "zzz");
  press(dom, "Enter");
  assert.equal(count(), "没有找到");
});

test("Esc 关掉搜索，页面回到一个字都没动过的样子", () => {
  const { dom, doc } = search("<p>前 alpha 后</p>", "alpha");
  press(dom, "Escape");
  assert.equal(doc.querySelector(".lfv-search"), null);
  assert.equal(doc.querySelectorAll("mark.lfv-hit").length, 0);
  assert.equal(doc.querySelector("p")?.textContent, "前 alpha 后");
  assert.equal(doc.querySelector("p")?.childNodes.length, 1, "拆完要合并回一个文本节点");
});

test("关掉之后按键不再被搜索接管", () => {
  const { dom, doc } = search("<p>alpha</p>", "alpha");
  press(dom, "Escape");
  const event = new dom.window.KeyboardEvent("keydown", { key: "Enter", cancelable: true, bubbles: true });
  doc.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
});

test("再次打开不会开出第二个搜索框", () => {
  const { doc } = search("<p>alpha</p>", "alpha");
  openSearch(doc);
  assert.equal(doc.querySelectorAll(".lfv-search").length, 1);
});

test("换词重搜：旧标记先拆干净再打新的", () => {
  const { dom, doc, input } = search("<p>alpha beta</p>", "alpha");
  input.value = "beta";
  input.dispatchEvent(new dom.window.Event("input"));
  const marks = Array.from(doc.querySelectorAll("mark.lfv-hit"), (m) => m.textContent);
  assert.deepEqual(marks, ["beta"]);
});
