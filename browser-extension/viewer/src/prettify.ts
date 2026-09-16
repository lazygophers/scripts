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

/** markdown 类的扩展名。`mdx` 也走文档视图，只是组件位置换成占位块。 */
const MARKDOWN_EXTS = new Set(["md", "markdown", "mdx"]);

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
  const render = module.renderMarkdown as (d: Document, t: string, mdx: boolean) => HTMLElement;
  const article = render(doc, text, extOf(doc.URL) === "mdx");
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

  wireLocalLinks(doc, host);
  host.classList.add("lfv-rendered");
  scrollToHash(doc);
}

/**
 * 文档里指向本地文本文件的链接：点了先探一下文件在不在，在就跳过去，不在就当场说清楚。
 *
 * 跳转本身不需要别的花样——新页面还是 `file://`，内容脚本照样接管并渲染，
 * 前进后退也就自然是浏览器原来的那一套。只有「文件不存在」这一种要拦，
 * 因为那时浏览器给的是它自己的报错页，内容脚本进不去，话就没处说。
 *
 * 网络地址、图片压缩包这类 viewer 不管的扩展名、页内锚点，一律不拦。
 */
function wireLocalLinks(doc: Document, host: HTMLElement): void {
  host.addEventListener("click", (event) => {
    const mouse = event as MouseEvent;
    // 新标签页打开、中键、右键都交回浏览器。
    if (mouse.defaultPrevented || mouse.button !== 0 || mouse.metaKey || mouse.ctrlKey) return;

    const link = (event.target as Element | null)?.closest?.("a");
    const href = link?.getAttribute("href");
    if (href === null || href === undefined) return;
    // 提示里那个「仍然打开」是用户明确说了要去，不再探第二遍。
    if (link?.classList.contains("lfv-anyway")) return;

    const target = new URL(href, doc.URL);
    if (target.protocol !== "file:") return;
    if (!WHITELIST_EXTS.has(extOf(target.href))) return;
    // 同一份文件里的锚点跳转是浏览器的活，不该走这条路。
    if (target.href.split("#")[0] === doc.URL.split("#")[0]) return;

    event.preventDefault();
    void follow(doc, host, target.href);
  });
}

async function follow(doc: Document, host: HTMLElement, url: string): Promise<void> {
  if (await exists(url)) {
    // `open(url, "_self")` 和 `location.assign` 在浏览器里是同一件事：当前标签页导航，留下一条历史。
    doc.defaultView?.open(url, "_self");
    return;
  }
  host.querySelector(".lfv-missing")?.remove();
  host.prepend(missingNote(doc, url));
}

/**
 * 探一下这个本地文件在不在。
 *
 * 需要: 读不到有两种可能——文件真的不存在，或者这一档上下文没有 `file://` 的读权限。
 * 两者在 `fetch` 这一层分不开，所以提示里附一个「仍然打开」，真是权限问题时用户不会被卡住。
 * 到底是哪一种，留给 16 票在真 Chrome 里跑一次确认。
 */
async function exists(url: string): Promise<boolean> {
  try {
    return (await fetch(url)).ok;
  } catch {
    return false;
  }
}

function missingNote(doc: Document, url: string): HTMLElement {
  const note = doc.createElement("div");
  note.className = "lfv-missing";
  const name = decodeURIComponent(url.split("/").pop() ?? url);
  note.append(`找不到这个文件：${name}`);

  const anyway = doc.createElement("a");
  anyway.className = "lfv-anyway";
  anyway.href = url;
  anyway.textContent = "仍然打开";
  note.append(" ", anyway);
  return note;
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
