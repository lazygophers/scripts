/**
 * json / yaml 的折叠树。和高亮、markdown 一样单独打成一个 bundle，打开这两类文件才加载。
 *
 * 展开收起本身没写一行 JS：每个对象和数组就是一个 `<details>`，三角和记忆状态都是浏览器自带的。
 */

import { parse as parseYaml } from "yaml";

/** 解析失败时要告诉用户的两件事：错在第几行、错的是什么。 */
export type DataError = { line: number; message: string };

/** 解析结果：成功给值，失败给行号和原因。值本身可能是 `null`，所以用 `ok` 分档而不是判空。 */
export type Parsed = { ok: true; value: unknown } | { ok: false; error: DataError };

/** V8 的 JSON 报错有时直接报行号：`... in JSON at position 9 (line 3 column 1)`。 */
const JSON_LINE = /\(line (\d+)/;

/** 只报字符位置的那一种：`... at position 9`。数一下前面有几个换行就是行号。 */
const JSON_POS = /position (\d+)/;

/**
 * 两样都不报的那一种：`Unexpected token ',', ..."1,\n  "b": ,\n}\n" is not valid JSON`——
 * 引号里是出错处附近的一小段原文，开头的 `...` 表示前面还有被截掉的字符。
 */
const JSON_SNIPPET = /(\.\.\.)?"([\s\S]*)" is not valid JSON$/;

/** V8 截断那一段原文时，截断处离出错字符固定 10 个字符（node 22 / V8 12 实测）。 */
const SNIPPET_LEAD = 10;

export function parseData(text: string, yaml: boolean): Parsed {
  try {
    return { ok: true, value: yaml ? parseYaml(text) : JSON.parse(text) };
  } catch (thrown) {
    return { ok: false, error: locate(thrown as Error, text) };
  }
}

/**
 * 从解析器抛出的错误里挖出行号。
 *
 * `yaml` 包自己带 `linePos`（<https://eemeli.org/yaml/#errors>）。`JSON.parse` 三种报法都试一遍，
 * 都问不出来时给 1，至少不是「一片空白」，原文照样摆在下面让用户自己看。
 */
function locate(error: Error, text: string): DataError {
  const message = error.message;
  const linePos = (error as { linePos?: [{ line: number }] }).linePos?.[0]?.line;
  if (linePos !== undefined) return { line: linePos, message };

  const reported = JSON_LINE.exec(message)?.[1];
  if (reported !== undefined) return { line: Number(reported), message };

  const at = JSON_POS.exec(message)?.[1];
  const position = at === undefined ? snippetPosition(message, text) : Number(at);
  return { line: text.slice(0, position).split("\n").length, message };
}

/** 报错里只剩一小段原文时，把它在全文里找回来，换算成出错字符的位置。找不回来就当开头。 */
function snippetPosition(message: string, text: string): number {
  const matched = JSON_SNIPPET.exec(message);
  if (matched === null) return 0;
  const at = text.indexOf(matched[2] ?? "");
  if (at < 0) return 0;
  return matched[1] === undefined ? at : at + SNIPPET_LEAD;
}

/** 一个标量在树里显示成什么样：文本和颜色类名。 */
function scalar(doc: Document, value: unknown): HTMLElement {
  const leaf = doc.createElement("span");
  const type = value === null ? "null" : typeof value;
  leaf.className = `lfv-${type === "number" || type === "bigint" ? "num" : type === "boolean" ? "bool" : type === "string" ? "str" : "null"}`;
  leaf.textContent = typeof value === "string" ? JSON.stringify(value) : String(value);
  return leaf;
}

/** 对象和数组各自的括号与计数写法。 */
function summaryOf(value: object): string {
  const count = Array.isArray(value) ? value.length : Object.keys(value).length;
  const [open, close] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return `${open}…${close} ${count} 项`;
}

/** 子节点在路径里那一截：数组用下标，对象用键名。 */
function step(path: string, key: string, array: boolean): string {
  return array ? `${path}[${key}]` : `${path}.${key}`;
}

/**
 * 一个节点。标量直接是一行，对象和数组是一个 `<details>`。
 *
 * 键上挂着自己的完整路径，点一下就能复制走。
 */
function node(doc: Document, key: string | null, value: unknown, path: string): HTMLElement {
  const label = doc.createElement("span");
  if (key !== null) {
    label.className = "lfv-key";
    label.textContent = `${key}: `;
    label.dataset["path"] = path;
    label.title = "点一下复制这个节点的路径";
  }

  if (value === null || typeof value !== "object") {
    const line = doc.createElement("div");
    line.className = "lfv-node";
    line.append(label, scalar(doc, value));
    return line;
  }

  const box = doc.createElement("details");
  box.className = "lfv-node";
  box.open = true;
  const head = doc.createElement("summary");
  const count = doc.createElement("span");
  count.className = "lfv-count";
  count.textContent = summaryOf(value);
  head.append(label, count);
  box.append(head);

  const array = Array.isArray(value);
  const kids = array
    ? (value as unknown[]).map((item, i) => [String(i), item] as const)
    : Object.entries(value as Record<string, unknown>);
  for (const [name, item] of kids) box.append(node(doc, name, item, step(path, name, array)));
  return box;
}

/** 整棵树。根节点没有键名，路径从 `$` 起算，和 jq 的写法一致。 */
export function renderTree(doc: Document, value: unknown): HTMLElement {
  const tree = doc.createElement("div");
  tree.className = "lfv-tree";
  tree.append(node(doc, null, value, "$"));
  wireCopyPath(doc, tree);
  return tree;
}

/** 点键名复制路径，复制完那一行短暂变成「已复制」。 */
function wireCopyPath(doc: Document, tree: HTMLElement): void {
  tree.addEventListener("click", (event) => {
    const key = (event.target as Element | null)?.closest?.(".lfv-key") as HTMLElement | null;
    const path = key?.dataset["path"];
    if (key === null || key === undefined || path === undefined) return;
    // 键在 `<summary>` 里，点了会把那一节收起来，这不是用户想要的。
    event.preventDefault();

    const before = key.textContent ?? "";
    void doc.defaultView?.navigator.clipboard.writeText(path).then(() => {
      key.textContent = "已复制 ";
      doc.defaultView?.setTimeout(() => {
        key.textContent = before;
      }, 1200);
    });
  });
}

/** 解析失败时顶上那条：第几行、什么错。 */
export function renderError(doc: Document, error: DataError): HTMLElement {
  const note = doc.createElement("div");
  note.className = "lfv-parse-error";
  note.textContent = `第 ${error.line} 行有语法错误：${error.message}`;
  return note;
}
