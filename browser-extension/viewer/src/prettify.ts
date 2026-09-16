/**
 * viewer 的唯一入口，也是唯一的测试缝：判定、类型推断、分派、渲染、挂交互全在里面。
 *
 * 这一版只有「原样文本」一种渲染结果（排版好的纯文本），按类型分流的渲染器是后面的票。
 */

/** 纯文本类的 contentType。json / yaml / xml 的各种变体都算文本，所以单列。 */
export const TEXT_TYPE = /^text\/|\/(json|[a-z-]*yaml|[a-z-]*xml)$/;

/** 超过这个大小不自动美化，只给一个「强制美化」按钮，免得浏览器卡住。 */
const MAX_BYTES = 2 * 1024 * 1024;

/** 明确是网页的扩展名：这些一个字节都不碰。 */
const WEBPAGE_EXTS = new Set(["html", "htm", "xhtml", "svg", "xml"]);

/** 白名单：这些直接接管。 */
const WHITELIST_EXTS = new Set([
  "md", "markdown", "mdx", "txt", "log", "json", "yaml", "yml", "csv",
  "toml", "ini", "conf", "go", "py", "ts", "tsx", "js", "jsx", "rs",
  "java", "c", "h", "cpp", "hpp", "cs", "rb", "php", "sh", "bash", "zsh",
  "sql", "css", "scss",
]);

export type Kind = "webpage" | "whitelist" | "unknown";

/**
 * 三条同时成立才算纯文本页：内容类型是文本类、正文只有一坨预格式化文本、页面没有标题。
 *
 * 浏览器给 `file:///x.go` 这类文件生成的页面正好长这样；真正的网页至少会输在标题或结构上。
 */
export function isPlainTextPage(doc: Document): boolean {
  if (!TEXT_TYPE.test(doc.contentType)) return false;
  if (doc.title !== "") return false;
  const kids = doc.body.children;
  return kids.length === 1 && kids[0]?.tagName === "PRE";
}

/** 按 URL 扩展名分三档。取不到扩展名就算拿不准。 */
export function classify(url: string): Kind {
  const name = url.split(/[?#]/)[0]?.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  if (WEBPAGE_EXTS.has(ext)) return "webpage";
  if (WHITELIST_EXTS.has(ext)) return "whitelist";
  return "unknown";
}

export function prettify(doc: Document): void {
  if (!isPlainTextPage(doc)) return;
  const kind = classify(doc.URL);
  if (kind === "webpage") return;

  const pre = doc.body.children[0] as HTMLElement;
  const text = pre.textContent ?? "";
  if (kind === "unknown") return offer(doc, pre, "美化一下");
  if (new TextEncoder().encode(text).length > MAX_BYTES) {
    return offer(doc, pre, "强制美化");
  }
  mount(doc, pre, true);
}

/**
 * 拿不准或文件太大时只浮一个按钮，别的什么都不动——连样式表都不加载，
 * 这样「DOM 未被改动，或只多了那个按钮」这句断言才站得住。
 */
function offer(doc: Document, pre: HTMLElement, label: string): void {
  const button = makeButton(doc, label, () => {
    button.remove();
    mount(doc, pre, true);
  });
  doc.body.append(button);
}

/**
 * 切换原始 / 美化。原始那一档放回的是最初那个 `<pre>` 节点本身，
 * 所以「原始」和浏览器自己的显示逐字节相同。选择不记忆，刷新就回到默认。
 */
function mount(doc: Document, pre: HTMLElement, pretty: boolean): void {
  ensureStylesheet(doc);
  const button = makeButton(doc, pretty ? "原始" : "美化", () =>
    mount(doc, pre, !pretty),
  );
  doc.documentElement.classList.toggle("lfv-on", pretty);
  doc.body.replaceChildren(pretty ? render(doc, pre.textContent ?? "") : pre, button);
}

/** 这一版的唯一渲染器：排版好的纯文本。 */
function render(doc: Document, text: string): HTMLElement {
  const view = doc.createElement("pre");
  view.className = "lfv-text";
  view.textContent = text;
  return view;
}

function makeButton(doc: Document, label: string, onClick: () => void): HTMLElement {
  const button = doc.createElement("button");
  button.className = "lfv-toggle";
  button.textContent = label;
  // 内联样式而不是靠样式表：不接管的页面上这个按钮是唯一的改动，不该顺带拖进一张 CSS。
  button.setAttribute(
    "style",
    "position:fixed;top:8px;right:8px;z-index:2147483647;opacity:0.45;" +
      "padding:4px 10px;border-radius:6px;border:1px solid #666;" +
      "background:#1c1c1f;color:#eee;font:12px/1.4 system-ui,sans-serif;cursor:pointer",
  );
  button.addEventListener("click", onClick);
  return button;
}

const STYLE_ID = "lfv-style";

function ensureStylesheet(doc: Document): void {
  if (doc.getElementById(STYLE_ID)) return;
  const link = doc.createElement("link");
  link.id = STYLE_ID;
  link.setAttribute("rel", "stylesheet");
  link.setAttribute("href", chrome.runtime.getURL("viewer.css"));
  doc.head.append(link);
}
