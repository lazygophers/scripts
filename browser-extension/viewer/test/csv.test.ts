import assert from "node:assert/strict";
import test from "node:test";

import { parseCsv, renderTable } from "../src/csv.ts";
import { page } from "./mock.ts";

/**
 * RFC 4180 的切分规则（<https://www.rfc-editor.org/rfc/rfc4180#section-2>）和
 * 表格渲染。这两块此前没有测试，而它们是打开一个 `.csv` 时唯一跑的代码。
 */

test("普通行按逗号切分", () => {
  assert.deepEqual(parseCsv("a,b,c\n1,2,3"), [["a", "b", "c"], ["1", "2", "3"]]);
});

test("引号里的逗号和换行都是正文", () => {
  assert.deepEqual(parseCsv('name,note\n"Doe, John","第一行\n第二行"'), [
    ["name", "note"],
    ["Doe, John", "第一行\n第二行"],
  ]);
});

test("两个连着的双引号代表一个双引号", () => {
  assert.deepEqual(parseCsv('a\n"她说""好"""'), [["a"], ['她说"好"']]);
});

test("CRLF 算一个换行，不是两个", () => {
  assert.deepEqual(parseCsv("a,b\r\n1,2\r\n"), [["a", "b"], ["1", "2"]]);
});

test("末尾的换行不算新的一行", () => {
  assert.deepEqual(parseCsv("a,b\n1,2\n"), [["a", "b"], ["1", "2"]]);
});

test("空文件切出空表", () => {
  assert.deepEqual(parseCsv(""), []);
});

test("空字段保留位置，不被吃掉", () => {
  assert.deepEqual(parseCsv("a,,c\n,,"), [["a", "", "c"], ["", "", ""]]);
});

test("列数以表头为准：多的截掉、缺的补空并标出来", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [
    ["a", "b"],
    ["1", "2", "3"],
    ["1"],
  ]);
  const rows = table.querySelectorAll("tbody tr");
  assert.equal(rows.length, 2);
  assert.equal(rows[0]?.querySelectorAll("td").length, 2, "多出来的格子要截掉");
  assert.equal(rows[0]?.className, "lfv-row-ragged");
  assert.equal(rows[1]?.querySelectorAll("td")[1]?.className, "lfv-cell-missing");
  assert.equal(rows[1]?.querySelectorAll("td")[1]?.textContent, "");
});

test("空表也渲染成一张表，不报错", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, []);
  assert.equal(table.querySelectorAll("th").length, 0);
  assert.equal(table.querySelectorAll("tbody tr").length, 0);
});

test("每个表头带一个排序标签和一个拖宽把手", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["a", "b"], ["1", "2"]]);
  const heads = table.querySelectorAll("th");
  assert.equal(heads.length, 2);
  assert.equal(heads[1]?.dataset["column"], "1");
  assert.equal(heads[1]?.querySelector(".lfv-grip")?.getAttribute("data-column"), "1");
  assert.equal(heads[1]?.querySelector(".lfv-th-label")?.textContent, "b");
});

/** 点一下表头排序，再点一下反向；读出来的是当前 tbody 的行序。 */
function sortBy(table: HTMLElement, column: number, dom: ReturnType<typeof page>): string[] {
  const th = table.querySelectorAll("th")[column] as HTMLElement;
  const label = th.querySelector(".lfv-th-label") as HTMLElement;
  label.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  return Array.from(table.querySelectorAll("tbody tr"), (tr) => tr.querySelectorAll("td")[column]?.textContent ?? "");
}

test("整列都是数字时按数值排，不是按字符串排", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["10"], ["9"], ["100"]]);
  assert.deepEqual(sortBy(table, 0, dom), ["9", "10", "100"]);
});

test("再点一次同一列就反向，箭头跟着走", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["10"], ["9"], ["100"]]);
  sortBy(table, 0, dom);
  assert.deepEqual(sortBy(table, 0, dom), ["100", "10", "9"]);
  assert.equal(table.querySelectorAll("th")[0]?.getAttribute("data-sort"), "desc");
});

test("非数字列按文字排，空格子一律排在最后", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["s"], ["b"], [""], ["a"]]);
  assert.deepEqual(sortBy(table, 0, dom), ["a", "b", ""]);
});

test("拖把手的那一下不触发排序", () => {
  const dom = page("");
  const table = renderTable(dom.window.document, [["n"], ["10"], ["9"]]);
  const grip = table.querySelector(".lfv-grip") as HTMLElement;
  grip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(
    Array.from(table.querySelectorAll("tbody td"), (td) => td.textContent),
    ["10", "9"],
    "顺序应保持原样",
  );
  assert.equal(table.querySelectorAll("th")[0]?.hasAttribute("data-sort"), false);
});
