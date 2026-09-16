/**
 * csv 表格。和折叠树一样是个单独的 bundle，打开 `.csv` 才加载。
 *
 * 表头钉在顶部、行间隔底色都交给 CSS（`position: sticky` 和 `:nth-child`），
 * 这里只做三件 CSS 做不了的事：切分字段、点表头排序、拖列边界改列宽。
 */

/**
 * 按 RFC 4180 切分（<https://www.rfc-editor.org/rfc/rfc4180#section-2>）：
 * 字段可以用双引号包起来，包起来之后里面的逗号和换行都是正文，两个连着的双引号代表一个双引号。
 *
 * 一次扫完整个文件，不先按行拆——引号里的换行本来就不是行的结束。
 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  // 末尾那个换行不算新的一行。
  const body = text.replace(/\r?\n$/, "");

  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (quoted) {
      if (ch !== '"') field += ch;
      else if (body[i + 1] === '"') {
        field += '"';
        i += 1;
      } else quoted = false;
      continue;
    }
    if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      // `\r\n` 是一个换行，别当成两个。
      if (ch === "\r" && body[i + 1] === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += ch;
  }

  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/** 整列都是数字时按数值比大小，否则按文字。空格子一律排在最后。 */
function comparator(rows: string[][], column: number): (a: string[], b: string[]) => number {
  const values = rows.map((r) => r[column] ?? "");
  const numeric = values.every((v) => v === "" || (v.trim() !== "" && !Number.isNaN(Number(v))));
  return (a, b) => {
    const [x, y] = [a[column] ?? "", b[column] ?? ""];
    if (x === "" || y === "") return x === y ? 0 : x === "" ? 1 : -1;
    return numeric ? Number(x) - Number(y) : x.localeCompare(y);
  };
}

/**
 * 表头一行加正文若干行。列数以表头为准：多出来的格子截掉，缺的补空并把那一行标出来。
 *
 * 空文件给一张空表，不报错。
 */
export function renderTable(doc: Document, rows: string[][]): HTMLElement {
  const head = rows[0] ?? [];
  const body = rows.slice(1);

  const table = doc.createElement("table");
  table.className = "lfv-table";
  const thead = doc.createElement("thead");
  const headRow = doc.createElement("tr");
  for (const [i, name] of head.entries()) headRow.append(headCell(doc, name, i));
  thead.append(headRow);

  const tbody = doc.createElement("tbody");
  for (const line of body) tbody.append(bodyRow(doc, line, head.length));
  table.append(thead, tbody);

  wireSort(doc, table, head.length);
  wireResize(doc, table);
  return table;
}

function headCell(doc: Document, name: string, index: number): HTMLElement {
  const cell = doc.createElement("th");
  const label = doc.createElement("span");
  label.className = "lfv-th-label";
  label.textContent = name;
  label.title = "点一下按这一列排序";

  // 拖动它改列宽。做成单独一个节点，才不会和「点表头排序」抢同一次点击。
  const grip = doc.createElement("span");
  grip.className = "lfv-grip";
  grip.dataset["column"] = String(index);

  cell.dataset["column"] = String(index);
  cell.append(label, grip);
  return cell;
}

function bodyRow(doc: Document, line: string[], width: number): HTMLElement {
  const tr = doc.createElement("tr");
  // 格子数对不上的行照样渲染，只是标出来：缺的留空，多的截掉。
  if (line.length !== width) tr.className = "lfv-row-ragged";
  for (let i = 0; i < width; i += 1) {
    const td = doc.createElement("td");
    const value = line[i];
    if (value === undefined) td.className = "lfv-cell-missing";
    td.textContent = value ?? "";
    tr.append(td);
  }
  return tr;
}

/** 点表头排序，再点一次反向。当前排序列上留个箭头，看得出正按哪一列排。 */
function wireSort(doc: Document, table: HTMLElement, width: number): void {
  const state = { column: -1, descending: false };
  table.querySelector("thead")?.addEventListener("click", (event) => {
    const cell = (event.target as Element | null)?.closest?.("th") as HTMLElement | null;
    const column = Number(cell?.dataset["column"] ?? NaN);
    if (Number.isNaN(column) || column >= width) return;
    // 拖列宽的那一下不是排序。
    if ((event.target as Element).classList.contains("lfv-grip")) return;

    state.descending = state.column === column ? !state.descending : false;
    state.column = column;

    // 排的是行节点本身，不是重建：标记、列宽这些都跟着原样走。
    const tbody = table.querySelector("tbody") as HTMLTableSectionElement;
    const lines = Array.from(tbody.rows, (r) => Array.from(r.cells, (c) => c.textContent ?? ""));
    const compare = comparator(lines, column);
    const order = lines.map((_, i) => i);
    order.sort((a, b) => compare(lines[a] ?? [], lines[b] ?? []));
    if (state.descending) order.reverse();
    const rows = Array.from(tbody.rows);
    tbody.replaceChildren(...order.map((i) => rows[i] as HTMLTableRowElement));

    for (const th of table.querySelectorAll("thead th")) th.removeAttribute("data-sort");
    cell?.setAttribute("data-sort", state.descending ? "desc" : "asc");
  });
}

/** 拖列与列之间那道边界改列宽。宽度写在 `<th>` 的行内样式上，整列跟着走。 */
function wireResize(doc: Document, table: HTMLElement): void {
  let cell: HTMLElement | null = null;
  let startX = 0;
  let startWidth = 0;

  table.addEventListener("mousedown", (event) => {
    const grip = event.target as HTMLElement;
    if (!grip.classList?.contains("lfv-grip")) return;
    event.preventDefault();
    cell = grip.parentElement;
    startX = (event as MouseEvent).clientX;
    startWidth = cell?.getBoundingClientRect().width ?? 0;
  });

  doc.addEventListener("mousemove", (event) => {
    if (cell === null) return;
    // 再窄也留 40px，免得一列被拖没了就再也抓不回来。
    cell.style.width = `${Math.max(40, startWidth + (event as MouseEvent).clientX - startX)}px`;
  });

  doc.addEventListener("mouseup", () => {
    cell = null;
  });
}
