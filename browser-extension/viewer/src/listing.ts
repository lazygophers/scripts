/**
 * 目录列表。又一个单独的 bundle，打开本地目录才加载。
 *
 * 浏览器自己那张索引页的结构是固定的（chromium `net/base/dir_header.html`：
 * `#tbody` 里每行三个格子，名字格里一个 `<a>`，大小和时间格把原始数值放在 `data-value` 上），
 * 所以这里先把行读成数据，再照自己的样子重画一张表。
 */

/** 一条目录项。`size` / `mtime` 是用来排序的原始数值，`sizeText` / `mtimeText` 是给人看的。 */
export type Entry = {
  name: string;
  url: string;
  dir: boolean;
  size: number;
  sizeText: string;
  mtime: number;
  mtimeText: string;
};

/** 按扩展名分的图标档位。图标本身是 CSS 里的一个字符，这里只决定挂哪个类名。 */
const ICONS: Record<string, string> = {
  go: "code", py: "code", ts: "code", tsx: "code", js: "code", jsx: "code",
  rs: "code", java: "code", c: "code", h: "code", cpp: "code", hpp: "code",
  cs: "code", rb: "code", php: "code", sh: "code", bash: "code", zsh: "code",
  sql: "code", css: "code", scss: "code", json: "code", yaml: "code", yml: "code",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image",
  svg: "image", avif: "image", bmp: "image", ico: "image",
  zip: "archive", tar: "archive", gz: "archive", tgz: "archive", bz2: "archive",
  xz: "archive", rar: "archive", "7z": "archive",
};

/** 能浮出预览的扩展名：只有纯文本才去读，图片和压缩包不碰。 */
const PREVIEWABLE = new Set([
  "md", "markdown", "mdx", "txt", "log", "json", "yaml", "yml", "csv",
  "toml", "ini", "conf", "go", "py", "ts", "tsx", "js", "jsx", "rs",
  "java", "c", "h", "cpp", "hpp", "cs", "rb", "php", "sh", "bash", "zsh",
  "sql", "css", "scss",
]);

/** 预览读多少字节就够：前几行而已，不整个文件拉下来。 */
const PREVIEW_BYTES = 4096;

/** 预览显示几行。 */
const PREVIEW_LINES = 5;

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** 浏览器那张表读成数据。传进来的是 `#tbody`，已经从页面上摘下来也照样能读。 */
export function parseListing(tbody: HTMLElement): Entry[] {
  return Array.from(tbody.querySelectorAll("tr"), (row) => {
    const cells = row.querySelectorAll("td");
    const link = row.querySelector("a");
    const name = (link?.textContent ?? "").replace(/\/$/, "");
    const size = cells[1];
    const date = cells[2];
    return {
      name,
      url: (link as HTMLAnchorElement | null)?.href ?? "",
      dir: link?.classList.contains("dir") ?? false,
      size: Number(size?.dataset["value"] ?? 0),
      sizeText: size?.textContent ?? "",
      mtime: Number(date?.dataset["value"] ?? 0),
      mtimeText: date?.textContent ?? "",
    };
  }).filter((entry) => entry.name !== "");
}

/** 面包屑：当前路径的每一层都是一个可点的链接，点了直接跳到那一层。 */
function crumbs(doc: Document, path: string): HTMLElement {
  const bar = doc.createElement("nav");
  bar.className = "lfv-crumbs";

  const parts = path.split("/").filter((p) => p !== "");
  let href = "file://";
  const link = (text: string, url: string) => {
    const node = doc.createElement("a");
    node.className = "lfv-crumb";
    node.textContent = text;
    node.href = url;
    bar.append(node);
  };

  link("/", "file:///");
  for (const part of parts) {
    href += `/${part}`;
    link(part, `${href}/`);
  }
  return bar;
}

/** 顶部工具条：一个边打字边筛的过滤框，一个管隐藏文件的开关。 */
function toolbar(doc: Document, host: HTMLElement, table: HTMLElement): HTMLElement {
  const bar = doc.createElement("div");
  bar.className = "lfv-dir-tools";

  const filter = doc.createElement("input");
  filter.className = "lfv-dir-filter";
  filter.type = "search";
  filter.placeholder = "筛选文件名";
  filter.addEventListener("input", () => {
    const needle = filter.value.toLowerCase();
    for (const row of table.querySelectorAll("tbody tr")) {
      const name = (row as HTMLElement).dataset["name"] ?? "";
      (row as HTMLElement).hidden = !name.toLowerCase().includes(needle);
    }
  });

  const label = doc.createElement("label");
  label.className = "lfv-dir-dotfiles";
  const box = doc.createElement("input");
  box.type = "checkbox";
  box.addEventListener("change", () => {
    host.classList.toggle("lfv-show-hidden", box.checked);
  });
  label.append(box, "显示隐藏文件");

  bar.append(filter, label);
  return bar;
}

/** 三列都能排，点一下正序，再点一下反序。名字按文字比，大小和时间按数值比。 */
type Key = "name" | "size" | "mtime";

function sorted(entries: Entry[], key: Key, descending: boolean): Entry[] {
  const out = [...entries].sort((a, b) =>
    key === "name" ? a.name.localeCompare(b.name) : (a[key] as number) - (b[key] as number),
  );
  return descending ? out.reverse() : out;
}

function row(doc: Document, entry: Entry): HTMLElement {
  const tr = doc.createElement("tr");
  tr.dataset["name"] = entry.name;
  // 点号开头的默认收起：只标出来，藏不藏交给 CSS 和上面那个开关。
  if (entry.name.startsWith(".")) tr.className = "lfv-dot";

  const nameCell = doc.createElement("td");
  const link = doc.createElement("a");
  const kind = entry.dir ? "dir" : (ICONS[extOf(entry.name)] ?? "file");
  link.className = `lfv-entry lfv-icon-${kind}`;
  link.textContent = entry.dir ? `${entry.name}/` : entry.name;
  link.href = entry.url;
  nameCell.append(link);

  const sizeCell = doc.createElement("td");
  sizeCell.className = "lfv-dir-size";
  sizeCell.textContent = entry.dir ? "" : entry.sizeText;

  const timeCell = doc.createElement("td");
  timeCell.className = "lfv-dir-time";
  timeCell.textContent = entry.mtimeText;

  tr.append(nameCell, sizeCell, timeCell);
  if (!entry.dir && PREVIEWABLE.has(extOf(entry.name))) wirePreview(doc, link, nameCell, entry);
  return tr;
}

/** 鼠标停在文本文件上时浮出开头几行。读一次就留着，移开再回来不重读。 */
function wirePreview(doc: Document, link: HTMLElement, cell: HTMLElement, entry: Entry): void {
  let box: HTMLElement | null = null;
  link.addEventListener("mouseenter", () => {
    if (box !== null) {
      box.hidden = false;
      return;
    }
    box = doc.createElement("pre");
    box.className = "lfv-preview";
    box.textContent = "读取中…";
    cell.append(box);
    void head(entry.url).then((text) => {
      if (box !== null) box.textContent = text;
    });
  });
  link.addEventListener("mouseleave", () => {
    if (box !== null) box.hidden = true;
  });
}

/** 取文件开头那一段。只读第一块数据就停，大文件不会整个拉下来。 */
async function head(url: string): Promise<string> {
  const response = await fetch(url);
  const reader = response.body?.getReader();
  const text = reader === undefined ? await response.text() : await firstChunk(reader);
  return text.slice(0, PREVIEW_BYTES).split("\n").slice(0, PREVIEW_LINES).join("\n");
}

/** 读第一块数据就把连接掐了，剩下的不要。 */
async function firstChunk(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<string> {
  const chunk = await reader.read();
  void reader.cancel();
  return new TextDecoder().decode(chunk.value);
}

/**
 * 整张列表：面包屑、工具条、表格。空目录不报错，给一句「这个目录是空的」。
 */
export function renderListing(doc: Document, entries: Entry[], path: string): HTMLElement {
  const host = doc.createElement("div");
  host.className = "lfv-dir-view";

  const table = doc.createElement("table");
  table.className = "lfv-dir-table";
  const thead = doc.createElement("thead");
  const headRow = doc.createElement("tr");
  const tbody = doc.createElement("tbody");

  // 默认按名字正序，和浏览器自己那张索引表一致。
  const state: { key: Key; descending: boolean } = { key: "name", descending: false };
  const fill = () => {
    tbody.replaceChildren(
      ...sorted(entries, state.key, state.descending).map((entry) => row(doc, entry)),
    );
  };

  const columns: [Key, string][] = [["name", "名称"], ["size", "大小"], ["mtime", "修改时间"]];
  for (const [key, name] of columns) {
    const cell = doc.createElement("th");
    cell.textContent = name;
    cell.dataset["column"] = key;
    cell.addEventListener("click", () => {
      state.descending = state.key === key ? !state.descending : false;
      state.key = key;
      for (const other of headRow.children) other.removeAttribute("data-sort");
      cell.setAttribute("data-sort", state.descending ? "desc" : "asc");
      fill();
    });
    headRow.append(cell);
  }

  fill();
  thead.append(headRow);
  table.append(thead, tbody);
  host.append(crumbs(doc, path), toolbar(doc, host, table), table);

  if (entries.length === 0) {
    const note = doc.createElement("p");
    note.className = "lfv-dir-empty";
    note.textContent = "这个目录是空的";
    host.append(note);
  }
  return host;
}
