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

/** html 一类：悬停预览用 iframe 渲染出来看，不当纯文本读。 */
const HTML_EXTS = new Set(["html", "htm"]);

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
  const ext = extOf(entry.name);
  if (entry.dir || PREVIEWABLE.has(ext) || HTML_EXTS.has(ext)) wireEntryPopup(doc, link, entry);
  return tr;
}

/** 悬浮卡片认得的条目：名字、地址、是不是目录。目录列表的行和文件内的超链接都给得出这三样。 */
export type PopupEntry = Pick<Entry, "name" | "url" | "dir">;

/**
 * 鼠标停在条目上时浮出悬浮卡片：卡片角上是「复制文件名 / 完整路径」（目录是
 * 「复制文件夹名」），下面按类型铺预览——纯文本给开头几行，html 用沙箱 iframe
 * 渲染。读一次就留着，移开再回来不重读。
 *
 * 事件挂在文件名链接上而不是整个名字格：悬在格子的空白处不该弹卡。卡片是锚点的
 * 孩子（只是绝对定位浮在表/正文上），指针从名字挪进卡片点按钮时不算离开。
 * 目录列表和文件内超链接传的都是链接本身。
 */
export function wireEntryPopup(doc: Document, anchor: HTMLElement, entry: PopupEntry): void {
  // 卡片绝对定位在锚点下方，锚点自己得是个定位基准。
  anchor.classList.add("lfv-popup-anchor");
  let box: HTMLElement | null = null;
  anchor.addEventListener("mouseenter", () => {
    if (box !== null) {
      box.hidden = false;
      return;
    }
    box = doc.createElement("div");
    box.className = "lfv-preview";

    const tools = doc.createElement("div");
    tools.className = "lfv-preview-tools";
    if (entry.dir) {
      tools.append(copyButton(doc, "复制文件夹名", entry.name));
    } else {
      tools.append(copyButton(doc, "复制文件名", entry.name), copyButton(doc, "复制完整路径", entry.url));
    }
    box.append(tools);

    const ext = extOf(entry.name);
    if (!entry.dir && HTML_EXTS.has(ext)) {
      // 沙箱一个权限都不给：预览只是看一眼，脚本和导航都不许。
      const frame = doc.createElement("iframe");
      frame.className = "lfv-preview-frame";
      frame.setAttribute("sandbox", "");
      box.append(frame);
      // 读不到时把原因写在原地：空白 iframe 看不出是文件的问题还是扩展的问题。
      void raw(entry.url).then(
        (text) => {
          frame.srcdoc = text;
        },
        (error: Error) => {
          frame.replaceWith(failNote(doc, `渲染不出来：${error.message}`));
        },
      );
    } else if (!entry.dir && PREVIEWABLE.has(ext)) {
      const pre = doc.createElement("pre");
      // 「读取中…」先占位：读不到时把原因写在原地，免得看不出是文件的问题还是扩展的问题。
      pre.textContent = "读取中…";
      box.append(pre);
      void head(entry.url).then(
        (text) => {
          pre.textContent = text;
        },
        (error: Error) => {
          pre.textContent = `读不出这个文件：${error.message}`;
        },
      );
    }
    anchor.append(box);
  });
  anchor.addEventListener("mouseleave", () => {
    if (box !== null) box.hidden = true;
  });
}

/** 预览出错时顶替正文的一行小字。 */
function failNote(doc: Document, text: string): HTMLElement {
  const pre = doc.createElement("pre");
  pre.textContent = text;
  return pre;
}

/** 复制按钮：点一下抄进剪贴板，成没成写在按钮自己身上。 */
function copyButton(doc: Document, label: string, text: string): HTMLButtonElement {
  const button = doc.createElement("button");
  button.className = "lfv-copy";
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", (event) => {
    // 卡片可能挂在文件内超链接上：不拦的话这一下会顺带让浏览器跳转到链接目标。
    event.preventDefault();
    event.stopPropagation();

    void copyText(doc, text).then(
      () => {
        button.textContent = "已复制";
      },
      (error: Error) => {
        button.textContent = `复制失败：${error.message}`;
      },
    );
  });
  return button;
}

/** 剪贴板优先走 `navigator.clipboard`；file:// 页面上它常常拿不到授权，退到 `execCommand`。 */
async function copyText(doc: Document, text: string): Promise<void> {
  const clipboard = doc.defaultView?.navigator.clipboard;
  if (clipboard !== undefined) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // 授权被拒就走下面的老路，不打断。
    }
  }
  const scratch = doc.createElement("textarea");
  scratch.value = text;
  scratch.style.position = "fixed";
  scratch.style.opacity = "0";
  doc.body.append(scratch);
  scratch.select();
  if (!doc.execCommand("copy")) throw new Error("浏览器不让复制");
  scratch.remove();
}

/**
 * 取文件开头那一段。只读第一块数据就停，大文件不会整个拉下来。
 *
 * 内容脚本直接 fetch `file://` 会被 CORS 拦（file:// 页面的 origin 是 null），拦下时
 * 转给后台代读——那边带着 `host_permissions`，浏览器放行。返回的是没裁过的原文，
 * 裁几行留给 `head()`，html 渲染预览则整段进 iframe。
 */
async function raw(url: string): Promise<string> {
  let text: string;
  try {
    const response = await fetch(url);
    const reader = response.body?.getReader();
    text = reader === undefined ? await response.text() : await firstChunk(reader);
  } catch (direct) {
    // 裸写 `chrome.runtime` 在没有这个全局的环境（测试、Firefox 的页面世界）是
    // ReferenceError，会把真正要报的错盖掉，所以从 globalThis 上摸。
    const sendMessage = (globalThis as { chrome?: typeof chrome }).chrome?.runtime?.sendMessage;
    const reply = typeof sendMessage === "function"
      ? await sendMessage({ type: "lfv-head", url }) as { ok: boolean; text?: string } | undefined
      : undefined;
    if (reply === undefined || !reply.ok) throw direct;
    text = reply.text ?? "";
  }
  return text;
}async function head(url: string): Promise<string> {
  const text = await raw(url);
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
