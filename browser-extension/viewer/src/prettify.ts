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

/**
 * 扩展名到 highlight.js 语言名。只列白名单里真的是源码的那些。
 *
 * 不在表里就不着色，也不会去加载高亮库——markdown 归后面的票渲染，
 * `txt` / `log` / `csv` / `conf` 本来就没有语法。
 */
const LANGS: Record<string, string> = {
  go: "go", py: "python", ts: "typescript", tsx: "typescript",
  js: "javascript", jsx: "javascript", rs: "rust", java: "java",
  c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", rb: "ruby",
  php: "php", sh: "bash", bash: "bash", zsh: "bash", sql: "sql",
  css: "css", scss: "scss", ini: "ini", toml: "ini",
  json: "json", yaml: "yaml", yml: "yaml",
};

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

/** URL 的小写扩展名。没有扩展名、或者是 `.bashrc` 这种纯点开头的，都算没有。 */
function extOf(url: string): string {
  const name = url.split(/[?#]/)[0]?.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** 按 URL 扩展名分三档。取不到扩展名就算拿不准。 */
export function classify(url: string): Kind {
  const ext = extOf(url);
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

/** markdown 类的扩展名。`mdx` 暂时仍按纯文本，等方言那一票。 */
const MARKDOWN_EXTS = new Set(["md", "markdown"]);

/** 按扩展名分流：markdown 走文档视图，认得的源码走代码视图，其余仍是排版好的纯文本。 */
function render(doc: Document, text: string): HTMLElement {
  const ext = extOf(doc.URL);
  if (MARKDOWN_EXTS.has(ext)) return renderDocument(doc, text);
  const language = LANGS[ext];
  if (language === undefined) {
    const view = doc.createElement("pre");
    view.className = "lfv-text";
    view.textContent = text;
    return view;
  }
  return renderCode(doc, text, language);
}

/**
 * 代码视图：行号、正文、复制按钮三件套。
 *
 * 行号单独放一列而不是给每行包一个 span——高亮出来的标签会跨行，拆行要把它们逐个断开再接上；
 * 单独一列还顺带满足「行号不进剪贴板」：那一列 `user-select: none`，框选时选不中。
 */
function renderCode(doc: Document, text: string, language: string): HTMLElement {
  const wrap = doc.createElement("div");
  wrap.className = "lfv-code";

  const gutter = doc.createElement("pre");
  gutter.className = "lfv-gutter";
  gutter.setAttribute("aria-hidden", "true");
  // 末尾那个换行不算新的一行，空文件是 0 行。
  const lines = text === "" ? 0 : text.replace(/\n$/, "").split("\n").length;
  gutter.textContent = Array.from({ length: lines }, (_, i) => `${i + 1}`).join("\n");

  const view = doc.createElement("pre");
  view.className = "lfv-text";
  const code = doc.createElement("code");
  code.textContent = text;
  view.append(code);

  wrap.append(makeCopyButton(doc, text), gutter, view);
  void colorize(code, language);
  return wrap;
}

/**
 * markdown 视图：先挂一个空壳子，排版和净化在 markdown 包里异步做完再填进来。
 * `prettify` 因此仍然是同步的。填完打 `lfv-rendered` 标记，和 `lfv-colored` 同一个约定。
 */
function renderDocument(doc: Document, text: string): HTMLElement {
  const host = doc.createElement("div");
  host.className = "lfv-doc";
  void fillDocument(doc, host, text);
  return host;
}

async function fillDocument(doc: Document, host: HTMLElement, text: string): Promise<void> {
  const module = await import(chrome.runtime.getURL("markdown.js"));
  const article = (module.renderMarkdown as (d: Document, t: string) => HTMLElement)(doc, text);
  const toc = (module.renderToc as (d: Document, a: HTMLElement) => HTMLElement | null)(doc, article);
  host.replaceChildren(...(toc === null ? [article] : [toc, article]));

  // 围栏代码块上 marked 已经写好了 `language-xx`，复用同一个高亮包着色。
  const blocks = article.querySelectorAll<HTMLElement>("pre > code[class*='language-']");
  await Promise.all(
    Array.from(blocks, (code) => {
      const language = /language-([\w-]+)/.exec(code.className)?.[1];
      return language === undefined ? Promise.resolve() : colorize(code, language);
    }),
  );

  host.classList.add("lfv-rendered");
  scrollToHash(doc);
}

/** 带着 `#小节` 打开时，渲染完自动滚到那个标题。 */
function scrollToHash(doc: Document): void {
  const id = decodeURIComponent(doc.location.hash.slice(1));
  if (id === "") return;
  doc.getElementById(id)?.scrollIntoView();
}

/**
 * 着色是异步的：高亮库按需加载，所以先把纯文本挂上去，色彩晚一拍覆盖上来。
 *
 * 结束时打一个 `lfv-colored` 标记——不着色的情况也打，这样等它的人有确定的信号。
 */
async function colorize(code: HTMLElement, language: string): Promise<void> {
  const module = await import(chrome.runtime.getURL("highlight.js"));
  const html = (module.colorize as (c: string, l: string) => string | null)(
    code.textContent ?? "",
    language,
  );
  if (html !== null) code.innerHTML = html;
  code.classList.add("lfv-colored");
}

function makeCopyButton(doc: Document, text: string): HTMLElement {
  const button = doc.createElement("button");
  button.className = "lfv-copy";
  button.textContent = "复制";
  button.addEventListener("click", () => {
    void doc.defaultView?.navigator.clipboard.writeText(text).then(() => {
      button.textContent = "已复制";
    });
  });
  return button;
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
