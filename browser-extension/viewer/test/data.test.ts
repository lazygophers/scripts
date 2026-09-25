import assert from "node:assert/strict";
import test from "node:test";

import { parseData, renderError, renderTree } from "../src/data.ts";
import { page } from "./mock.ts";

/**
 * json / yaml 折叠树。行号定位那一段尤其值得钉住：V8 的 JSON 报错有三种写法
 * （报行号 / 只报字符位置 / 只给一小段原文），三条分支都靠正则从报错文本里挖，
 * 换一个 node 版本就可能悄悄退化成「第 1 行」。
 */

test("解析成功给出值本身", () => {
  assert.deepEqual(parseData('{"a":1}', false), { ok: true, value: { a: 1 } });
});

test("值是 null 也算成功，不被当成失败", () => {
  assert.deepEqual(parseData("null", false), { ok: true, value: null });
});

test("yaml 走 yaml 解析器", () => {
  assert.deepEqual(parseData("a: 1\nb:\n  - x\n", true), { ok: true, value: { a: 1, b: ["x"] } });
});

test("yaml 的报错带自己的行号", () => {
  const parsed = parseData("a: 1\n b: [\n", true);
  assert.equal(parsed.ok, false);
  if (parsed.ok) return;
  assert.ok(parsed.error.line >= 1);
  assert.ok(parsed.error.message.length > 0);
});

test("JSON 报错能定位到出错那一行", () => {
  const parsed = parseData('{\n  "a": 1,\n  "b": ,\n}\n', false);
  assert.equal(parsed.ok, false);
  if (parsed.ok) return;
  assert.equal(parsed.error.line, 3, `实际报在第 ${parsed.error.line} 行：${parsed.error.message}`);
});

test("完全问不出行号时退到第 1 行，而不是空白", () => {
  const parsed = parseData("", false);
  assert.equal(parsed.ok, false);
  if (parsed.ok) return;
  assert.equal(parsed.error.line, 1);
});

test("错误条写明第几行和原因", () => {
  const dom = page("");
  const note = renderError(dom.window.document, { line: 7, message: "Unexpected token" });
  assert.equal(note.className, "lfv-parse-error");
  assert.equal(note.textContent, "第 7 行有语法错误：Unexpected token");
});

test("标量按类型上色，字符串保留引号", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { s: "x", n: 1, b: true, z: null });
  const leaves = Array.from(tree.querySelectorAll("span[class^=lfv-]:not(.lfv-key):not(.lfv-count)"));
  const seen = leaves.map((el) => [el.className, el.textContent]);
  assert.deepEqual(seen, [
    ["lfv-str", '"x"'],
    ["lfv-num", "1"],
    ["lfv-bool", "true"],
    ["lfv-null", "null"],
  ]);
});

test("对象和数组各自的括号与计数", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { list: [1, 2, 3], map: { a: 1 } });
  const counts = Array.from(tree.querySelectorAll(".lfv-count"), (el) => el.textContent);
  assert.deepEqual(counts, ["{…} 2 项", "[…] 3 项", "{…} 1 项"]);
});

test("路径从 $ 起算，数组用下标、对象用键名", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { a: [{ b: 1 }] });
  const paths = Array.from(tree.querySelectorAll(".lfv-key"), (el) => el.getAttribute("data-path"));
  assert.deepEqual(paths, ["$.a", "$.a[0]", "$.a[0].b"]);
});

test("对象和数组是 details，默认展开", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { a: { b: 1 } });
  const boxes = tree.querySelectorAll("details.lfv-node");
  assert.equal(boxes.length, 2);
  for (const box of boxes) assert.equal((box as HTMLDetailsElement).open, true);
});

test("空对象和空数组照样是一个节点", () => {
  const dom = page("");
  const tree = renderTree(dom.window.document, { empty: {}, none: [] });
  const counts = Array.from(tree.querySelectorAll(".lfv-count"), (el) => el.textContent);
  assert.deepEqual(counts, ["{…} 2 项", "{…} 0 项", "[…] 0 项"]);
});

test("点键名复制路径，那一行短暂变成「已复制」", async () => {
  const dom = page("");
  const copied: string[] = [];
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: { writeText: async (text: string) => void copied.push(text) },
    configurable: true,
  });
  const tree = renderTree(dom.window.document, { a: 1 });
  dom.window.document.body.append(tree);

  const key = tree.querySelector(".lfv-key") as HTMLElement;
  key.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(copied, ["$.a"]);
  assert.equal(key.textContent, "已复制 ");
});
