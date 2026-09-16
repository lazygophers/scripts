/**
 * 页内搜索。又一个单独的 bundle，第一次按查找快捷键才加载。
 *
 * 为什么不用浏览器自带的查找：折叠起来的内容（目录树、日志里的 JSON、`<details>` 里的一切）
 * 浏览器搜不到，用户会以为文件里没这个词。这里自己走一遍文本节点，命中处包一个 `<mark>`，
 * 落在折叠节点里就把那一层打开。
 */

/** 命中处的标记。关掉搜索时按这个类名找回来、拆掉。 */
const HIT = "lfv-hit";

/** 当前那一处的额外标记，颜色和其余命中不同。 */
const CURRENT = "lfv-hit-current";

/** 把一段文本里所有命中处包成 `<mark>`。返回这一段里新加的标记。 */
function markText(doc: Document, node: Text, needle: string): HTMLElement[] {
  const text = node.data;
  const lower = text.toLowerCase();
  const marks: HTMLElement[] = [];
  const parts = doc.createDocumentFragment();

  let at = 0;
  for (;;) {
    const found = lower.indexOf(needle, at);
    if (found < 0) break;
    if (found > at) parts.append(text.slice(at, found));
    const mark = doc.createElement("mark");
    mark.className = HIT;
    mark.textContent = text.slice(found, found + needle.length);
    parts.append(mark);
    marks.push(mark);
    at = found + needle.length;
  }

  if (marks.length === 0) return marks;
  if (at < text.length) parts.append(text.slice(at));
  node.replaceWith(parts);
  return marks;
}

/** 整个页面找一遍。空词等于不找。 */
function markAll(doc: Document, root: HTMLElement, needle: string): HTMLElement[] {
  if (needle === "") return [];
  const texts: Text[] = [];
  const walker = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    const parent = (node as Text).parentElement;
    if (parent === null || parent.closest(`.${HIT}, .lfv-search`) !== null) continue;
    if (parent.tagName === "SCRIPT" || parent.tagName === "STYLE") continue;
    texts.push(node as Text);
  }
  return texts.flatMap((node) => markText(doc, node, needle.toLowerCase()));
}

/** 拆掉所有标记，页面回到原样。 */
function unmark(root: HTMLElement): void {
  for (const mark of Array.from(root.querySelectorAll(`.${HIT}`))) {
    const parent = mark.parentNode;
    mark.replaceWith(mark.textContent ?? "");
    (parent as Element | null)?.normalize();
  }
}

/** 命中落在折叠起来的节点里时，把沿途每一层都打开，再滚过去。 */
function reveal(hit: HTMLElement): void {
  for (
    let node: HTMLElement | null = hit.parentElement;
    node !== null;
    node = node.parentElement
  ) {
    if (node.tagName === "DETAILS") (node as HTMLDetailsElement).open = true;
  }
  hit.scrollIntoView?.({ block: "center" });
}

/**
 * 打开搜索框。已经开着就把焦点放回输入框，不再开第二个。
 *
 * 返回搜索框本身，测试和调用方都用得上。
 */
export function openSearch(doc: Document): HTMLElement {
  const root = doc.body;
  const existing = doc.querySelector(".lfv-search") as HTMLElement | null;
  if (existing !== null) {
    (existing.querySelector("input") as HTMLInputElement | null)?.focus();
    return existing;
  }

  const bar = doc.createElement("div");
  bar.className = "lfv-search";
  const input = doc.createElement("input");
  input.className = "lfv-search-input";
  input.type = "search";
  input.placeholder = "在这个文件里找";
  const count = doc.createElement("span");
  count.className = "lfv-search-count";
  bar.append(input, count);
  doc.body.append(bar);
  input.focus();

  let hits: HTMLElement[] = [];
  let current = 0;

  const show = () => {
    for (const hit of hits) hit.classList.remove(CURRENT);
    if (hits.length === 0) {
      count.textContent = input.value === "" ? "" : "没有找到";
      return;
    }
    const hit = hits[current] as HTMLElement;
    hit.classList.add(CURRENT);
    reveal(hit);
    count.textContent = `第 ${current + 1} 个 / 共 ${hits.length} 个`;
  };

  const search = () => {
    unmark(root);
    hits = markAll(doc, root, input.value);
    current = 0;
    show();
  };

  // 循环跳：最后一个再往下回到第一个。
  const step = (delta: number) => {
    if (hits.length === 0) return;
    current = (current + delta + hits.length) % hits.length;
    show();
  };

  const close = () => {
    unmark(root);
    bar.remove();
  };

  input.addEventListener("input", search);
  bar.addEventListener("keydown", (event) => {
    const key = (event as KeyboardEvent).key;
    if (!["Escape", "Enter", "ArrowDown", "ArrowUp"].includes(key)) return;
    // 上下箭头本来是在输入框里挪光标，这里改成跳命中。
    event.preventDefault();
    if (key === "Escape") return close();
    step(key === "ArrowUp" || (event as KeyboardEvent).shiftKey ? -1 : 1);
  });

  return bar;
}
