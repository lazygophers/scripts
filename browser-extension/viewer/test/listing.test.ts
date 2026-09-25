import assert from "node:assert/strict";
import test from "node:test";

import { parseListing, renderListing, type Entry } from "../src/listing.ts";
import { page } from "./mock.ts";

/**
 * 目录列表。读的是 chromium 自己那张索引页（`net/base/dir_header.html`）：
 * `#tbody` 每行三个格子，名字格里一个 `<a>`，大小和时间把原始数值放在
 * `data-value` 上。这份结构一变，整张列表就空了，所以值得钉住。
 */

/** 造一份浏览器索引页那样的 tbody。 */
function browserTbody(rows: [name: string, dir: boolean, size: string, sizeText: string, mtime: string, mtimeText: string][]) {
  const dom = page("<table><tbody id='tbody'></tbody></table>");
  const doc = dom.window.document;
  const tbody = doc.querySelector("#tbody") as HTMLElement;
  for (const [name, dir, size, sizeText, mtime, mtimeText] of rows) {
    const tr = doc.createElement("tr");
    const nameCell = doc.createElement("td");
    const link = doc.createElement("a");
    link.textContent = dir ? `${name}/` : name;
    link.href = `file:///tmp/${name}`;
    if (dir) link.className = "dir";
    nameCell.append(link);

    const sizeCell = doc.createElement("td");
    sizeCell.dataset["value"] = size;
    sizeCell.textContent = sizeText;

    const timeCell = doc.createElement("td");
    timeCell.dataset["value"] = mtime;
    timeCell.textContent = mtimeText;

    tr.append(nameCell, sizeCell, timeCell);
    tbody.append(tr);
  }
  return { dom, tbody };
}

test("目录项的斜杠不进名字，dir 由链接的类名判定", () => {
  const { tbody } = browserTbody([["src", true, "0", "", "1758000000", "昨天"]]);
  const [entry] = parseListing(tbody);
  assert.equal(entry?.name, "src");
  assert.equal(entry?.dir, true);
});

test("大小和时间取 data-value 当排序值，文本另存一份给人看", () => {
  const { tbody } = browserTbody([["a.txt", false, "2048", "2.0 kB", "1758000000", "昨天"]]);
  const [entry] = parseListing(tbody);
  assert.equal(entry?.size, 2048);
  assert.equal(entry?.sizeText, "2.0 kB");
  assert.equal(entry?.mtime, 1758000000);
  assert.equal(entry?.mtimeText, "昨天");
});

test("没有名字的行被丢掉（索引页的表头行长这样）", () => {
  const { dom, tbody } = browserTbody([["a.txt", false, "1", "1 B", "1", "x"]]);
  tbody.append(dom.window.document.createElement("tr"));
  assert.equal(parseListing(tbody).length, 1);
});

function entry(over: Partial<Entry> = {}): Entry {
  return {
    name: "a.txt", url: "file:///tmp/a.txt", dir: false,
    size: 1, sizeText: "1 B", mtime: 1, mtimeText: "x", ...over,
  };
}

test("面包屑把路径每一层都做成可点的链接", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [], "/Users/me/code");
  const crumbs = Array.from(host.querySelectorAll(".lfv-crumb"), (a) => [
    a.textContent, (a as HTMLAnchorElement).getAttribute("href"),
  ]);
  assert.deepEqual(crumbs, [
    ["/", "file:///"],
    ["Users", "file:///Users/"],
    ["me", "file:///Users/me/"],
    ["code", "file:///Users/me/code/"],
  ]);
});

test("空目录给一句话，不报错", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [], "/tmp");
  assert.equal(host.querySelector(".lfv-dir-empty")?.textContent, "这个目录是空的");
  assert.equal(host.querySelectorAll("tbody tr").length, 0);
});

test("按扩展名挂图标类，目录和不认识的扩展名各有档位", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [
    entry({ name: "main.go" }), entry({ name: "logo.png" }), entry({ name: "pack.tar" }),
    entry({ name: "notes.xyz" }), entry({ name: "src", dir: true }),
  ], "/tmp");
  const kinds = Array.from(host.querySelectorAll(".lfv-entry"), (a) => a.className);
  assert.deepEqual(kinds.sort(), [
    "lfv-entry lfv-icon-archive",
    "lfv-entry lfv-icon-code",
    "lfv-entry lfv-icon-dir",
    "lfv-entry lfv-icon-file",
    "lfv-entry lfv-icon-image",
  ]);
});

test("目录行的大小列留空，名字后面补斜杠", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [entry({ name: "src", dir: true, sizeText: "—" })], "/tmp");
  assert.equal(host.querySelector(".lfv-entry")?.textContent, "src/");
  assert.equal(host.querySelector(".lfv-dir-size")?.textContent, "");
});

test("点号开头的行被标出来，藏不藏交给 CSS", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [entry({ name: ".env" }), entry()], "/tmp");
  const marked = Array.from(host.querySelectorAll("tbody tr"), (tr) => tr.className);
  assert.deepEqual(marked, ["lfv-dot", ""]);
});

/** 点某一列表头，读出排序后的名字。 */
function sortBy(host: HTMLElement, key: string, dom: ReturnType<typeof page>): string[] {
  const th = host.querySelector(`th[data-column="${key}"]`) as HTMLElement;
  th.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  return Array.from(host.querySelectorAll("tbody tr"), (tr) => (tr as HTMLElement).dataset["name"] ?? "");
}

test("默认按名字正序", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [entry({ name: "b" }), entry({ name: "a" })], "/tmp");
  assert.deepEqual(
    Array.from(host.querySelectorAll("tbody tr"), (tr) => (tr as HTMLElement).dataset["name"]),
    ["a", "b"],
  );
});

test("大小按数值排，不是按显示文本排", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [
    entry({ name: "big", size: 900000, sizeText: "900 kB" }),
    entry({ name: "small", size: 9, sizeText: "9 B" }),
  ], "/tmp");
  assert.deepEqual(sortBy(host, "size", dom), ["small", "big"]);
});

test("同一列再点一次反序，排序标记只留在当前列", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [
    entry({ name: "a", mtime: 1 }), entry({ name: "b", mtime: 2 }),
  ], "/tmp");
  sortBy(host, "mtime", dom);
  assert.deepEqual(sortBy(host, "mtime", dom), ["b", "a"]);
  assert.equal(host.querySelector('th[data-column="mtime"]')?.getAttribute("data-sort"), "desc");
  assert.equal(host.querySelector('th[data-column="name"]')?.hasAttribute("data-sort"), false);
});

test("筛选框按名字隐藏行，不删行", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [entry({ name: "main.go" }), entry({ name: "readme.md" })], "/tmp");
  const filter = host.querySelector(".lfv-dir-filter") as HTMLInputElement;
  filter.value = "GO";
  filter.dispatchEvent(new dom.window.Event("input"));
  const hidden = Array.from(host.querySelectorAll("tbody tr"), (tr) => (tr as HTMLElement).hidden);
  assert.deepEqual(hidden, [false, true], "筛选不区分大小写，且只是隐藏");
});

test("隐藏文件开关切的是容器上的类名", () => {
  const dom = page("");
  const host = renderListing(dom.window.document, [entry({ name: ".env" })], "/tmp");
  const box = host.querySelector(".lfv-dir-dotfiles input") as HTMLInputElement;
  box.checked = true;
  box.dispatchEvent(new dom.window.Event("change"));
  assert.equal(host.classList.contains("lfv-show-hidden"), true);
});

test("鼠标停在文本文件上才读预览，图片不读", async () => {
  const dom = page("");
  const fetched: string[] = [];
  (globalThis as { fetch?: unknown }).fetch = async (url: string) => {
    fetched.push(url);
    return { body: undefined, text: async () => "第一行\n第二行\n第三行\n第四行\n第五行\n第六行" };
  };
  const host = renderListing(dom.window.document, [
    entry({ name: "a.md", url: "file:///tmp/a.md" }),
    entry({ name: "b.png", url: "file:///tmp/b.png" }),
  ], "/tmp");

  const links = host.querySelectorAll(".lfv-entry");
  links[1]?.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  assert.deepEqual(fetched, [], "图片不该触发读取");

  links[0]?.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(fetched, ["file:///tmp/a.md"]);
  assert.equal(host.querySelector(".lfv-preview")?.textContent, "第一行\n第二行\n第三行\n第四行\n第五行");
  delete (globalThis as { fetch?: unknown }).fetch;
});

test("读不出来时把原因写在预览框里，不卡在「读取中…」", async () => {
  const dom = page("");
  (globalThis as { fetch?: unknown }).fetch = async () => {
    throw new Error("NetworkError");
  };
  const host = renderListing(dom.window.document, [entry({ name: "a.md" })], "/tmp");
  host.querySelector(".lfv-entry")?.dispatchEvent(new dom.window.MouseEvent("mouseenter"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(host.querySelector(".lfv-preview")?.textContent, "读不出这个文件：NetworkError");
  delete (globalThis as { fetch?: unknown }).fetch;
});
