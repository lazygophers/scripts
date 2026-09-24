/**
 * viewer 的唯一入口，也是唯一的测试缝：判定、类型推断、分派、渲染、挂交互全在里面。
 *
 * 这一版只有「原样文本」一种渲染结果（排版好的纯文本），按类型分流的渲染器是后面的票。
 */

import { openDiagramPreview } from "./diagram-preview.ts";
import { onSettingsChange, readSettings, writeSettings } from "./settings.ts";
import { PALETTES, STYLES, applyTheme } from "./themes.ts";

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

/**
 * 判定「这是浏览器给本地目录生成的索引页」。
 *
 * 它不是纯文本页：有标题、内容是 `text/html`，所以判定规则单独一条。
 * 那张表是浏览器自己拼出来的，`#tbody` 和 `#theader` 两个 id 都写死在模板里
 * （chromium `net/base/dir_header.html`），两个都在才算数，别的网页碰不上。
 */
export function isDirectoryIndex(doc: Document): boolean {
  if (!doc.URL.startsWith("file://")) return false;
  return doc.querySelector("tbody#tbody") !== null && doc.querySelector("#theader") !== null;
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

/**
 * 这个页面展示的是哪个文件。
 *
 * 浏览器直接打开文件时，页面地址就是文件地址。开了强制拦截之后，文件是在扩展自己那张
 * 展示页里显示的，页面地址成了 `chrome-extension://…/viewer.html`，真地址由那张页面
 * 写在 `<html data-lfv-url>` 上——按扩展名分流、解析相对链接都得认真地址，不是页面地址。
 */
function sourceUrl(doc: Document): string {
  return doc.documentElement.dataset["lfvUrl"] ?? doc.URL;
}

export function prettify(doc: Document): void {
  if (isDirectoryIndex(doc)) return mountListing(doc);
  if (!isPlainTextPage(doc)) return;
  const kind = classify(sourceUrl(doc));
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
 * 扩展自己那张展示页的入口：文件已经取好了，直接照美化档渲染。
 *
 * 浏览器把这几类文件直接下载掉，内容脚本根本没机会出手，所以走这条路进来。
 */
export function show(doc: Document, url: string, text: string): void {
  doc.documentElement.dataset["lfvUrl"] = url;
  const pre = doc.createElement("pre");
  pre.textContent = text;
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
 * 切换原始 / 美化，外加一个来回切的按钮。
 *
 * 「原始」那一档放回的是浏览器原来那些节点本身，所以它和浏览器自己的显示逐字节相同。
 * 选择不记忆，刷新就回到默认。文件页和目录页的区别只在两边各摆什么节点，所以都走这里。
 */
function swap(doc: Document, plain: HTMLElement[], pretty: () => HTMLElement[], on: boolean): void {
  ensureStylesheet(doc);
  wireSearch(doc);
  wireTheme(doc);
  const button = makeButton(doc, on ? "原始" : "美化", () => swap(doc, plain, pretty, !on), "8px");
  // 主题按钮只在美化档出现：切回「原始」是要看浏览器原来的样子，那时页面上不该有主题。
  const nodes = on ? [...pretty(), makeThemeButton(doc), button] : [...plain, button];
  doc.documentElement.classList.toggle("lfv-on", on);
  doc.body.replaceChildren(...nodes);
}

/** 已经接上主题的页面。读一次存储、挂一次监听就够。 */
const themed = new WeakSet<Document>();

/**
 * 把存着的主题应用到这一页，并且盯着变更。
 *
 * 读存储是异步的，这期间页面用的是 `palette.css` 的默认深色——和「夜」接近，
 * 所以看不出闪烁。
 */
function wireTheme(doc: Document): void {
  if (themed.has(doc)) return;
  themed.add(doc);
  const paint = (settings: { theme: string; style: string; custom: Parameters<typeof applyTheme>[3] }) =>
    applyTheme(doc, settings.theme, settings.style, settings.custom);
  void readSettings().then(paint);
  onSettingsChange(paint);
}

/** 主题按钮：点开浮出主题列表，挨着「原始 / 美化」那个开关。 */
function makeThemeButton(doc: Document): HTMLElement {
  const button = makeButton(doc, "主题", () => toggleThemeMenu(doc, button), "64px");
  // 和「原始 / 美化」用不同的类名：那个类名是「切换渲染」的唯一标识，测试和用户脚本
  // 都按它找按钮，两个按钮共用一个类名会让 `querySelector` 抓错人。
  button.className = "lfv-theme-toggle";
  return button;
}

const MENU_ID = "lfv-theme-menu";

/** 菜单里的一段：一行小标题 + 若干可点的行。点完收起菜单，页面靠存储变更自己跟上。 */
function section(
  doc: Document,
  menu: HTMLElement,
  title: string,
  rows: readonly (readonly [string, string, string])[],
  current: string,
  pick: (id: string) => void,
): void {
  const head = doc.createElement("div");
  head.setAttribute(
    "style",
    "padding:6px 9px 3px;color:var(--muted-foreground);font-size:11px;letter-spacing:.08em",
  );
  head.textContent = title;
  menu.append(head);

  for (const [id, name, hint] of rows) {
    const row = doc.createElement("button");
    row.type = "button";
    row.dataset[title === "配色" ? "palette" : "style"] = id;
    row.dataset["theme"] = id;
    row.setAttribute(
      "style",
      "display:block;width:100%;padding:6px 9px;border:0;border-radius:6px;cursor:pointer;" +
        "text-align:left;font:inherit;background:none;color:inherit",
    );
    row.textContent = id === current ? `${name} ·` : name;
    row.title = hint;
    row.addEventListener("click", () => {
      pick(id);
      menu.remove();
    });
    menu.append(row);
  }
}

function toggleThemeMenu(doc: Document, anchor: HTMLElement): void {
  const open = doc.getElementById(MENU_ID);
  if (open !== null) {
    open.remove();
    return;
  }
  const menu = doc.createElement("div");
  menu.id = MENU_ID;
  menu.setAttribute(
    "style",
    "position:fixed;top:36px;right:8px;z-index:2147483647;min-width:200px;" +
      "padding:4px;border:1px solid var(--border);border-radius:8px;" +
      "background:var(--card);color:var(--foreground);" +
      "box-shadow:0 10px 30px rgb(0 0 0 / 0.25);font:13px/1.5 system-ui,sans-serif",
  );

  void readSettings().then((settings) => {
    // 两段：上面挑配色，下面挑版面。它们互相独立，所以分开列而不是列出 32 种组合。
    section(doc, menu, "配色", [
      ...PALETTES.map((p) => [p.id, p.name, p.hint] as const),
      ["custom", "自定义", "在设置页里自己调"] as const,
    ], settings.theme, (id) => void writeSettings({ theme: id }));
    section(doc, menu, "风格", STYLES.map((s) => [s.id, s.name, s.hint] as const),
      settings.style, (id) => void writeSettings({ style: id }));
  });

  anchor.after(menu);
  // 点别处就收起来。`once` 让这个监听自己退场，不用手动摘。
  doc.defaultView?.setTimeout(() => {
    doc.addEventListener("click", (event) => {
      if (!(event.target as Element | null)?.closest?.(`#${MENU_ID}`)) menu.remove();
    }, { once: true });
  }, 0);
}

/** 文件页：美化档每次都按原文重新渲染一遍，所以来回切不会留下上一次的状态。 */
function mount(doc: Document, pre: HTMLElement, pretty: boolean): void {
  swap(doc, [pre], () => [render(doc, pre.textContent ?? "")], pretty);
}

/**
 * 目录视图：先把浏览器那张表摘下来留着（切回「原始」时原样放回），再挂个空壳子异步填。
 */
function mountListing(doc: Document): void {
  const original = Array.from(doc.body.children) as HTMLElement[];
  const tbody = doc.querySelector("tbody#tbody") as HTMLElement;
  const host = doc.createElement("div");
  host.className = "lfv-dir";
  void fillListing(doc, host, tbody);
  // 列表是异步填进 `host` 的，来回切也复用这一个节点，不重新解析那张表。
  swap(doc, original, () => [host], true);
}

async function fillListing(doc: Document, host: HTMLElement, tbody: HTMLElement): Promise<void> {
  const module = await LAZY.listing();
  // 这时表已经从页面上摘下来了，但节点还在手里，照样读得出来。
  host.replaceChildren(module.renderListing(doc, module.parseListing(tbody), new URL(doc.URL).pathname));
  host.classList.add("lfv-rendered");
}

/**
 * 类型化懒加载：每个包的导出面以 `typeof import("./x.ts")` 的形状声明一次，加载点
 * 拿到的直接是有类型的 module —— 包里改名/换形，typecheck 当场炸，不用等运行时。
 * 2026-09-21 之前每个加载点都自己 `as` 一遍，签名漂移没有任何东西拦。
 *
 * 浏览器里加载的是 dist 里的独立文件（源文件只是类型的出处），地址要问扩展自己要；
 * 动态 `import()` 自带缓存，同一个包加载第二次不会再下载一遍。
 */
/** 导出给测试：懒加载面和构建入口的一致性由 `test/prettify.test.ts` 钉住。 */
export const LAZY = {
  listing: () => import(chrome.runtime.getURL("listing.js")) as Promise<typeof import("./listing.ts")>,
  data: () => import(chrome.runtime.getURL("data.js")) as Promise<typeof import("./data.ts")>,
  csv: () => import(chrome.runtime.getURL("csv.js")) as Promise<typeof import("./csv.ts")>,
  log: () => import(chrome.runtime.getURL("log.js")) as Promise<typeof import("./log.ts")>,
  markdown: () => import(chrome.runtime.getURL("markdown.js")) as Promise<typeof import("./markdown.ts")>,
  mermaid: () => import(chrome.runtime.getURL("mermaid.js")) as Promise<typeof import("./mermaid.ts")>,
  katex: () => import(chrome.runtime.getURL("katex.js")) as Promise<typeof import("./katex.ts")>,
  highlight: () => import(chrome.runtime.getURL("highlight.js")) as Promise<typeof import("./highlight.ts")>,
  search: () => import(chrome.runtime.getURL("search.js")) as Promise<typeof import("./search.ts")>,
};

/** markdown 类的扩展名。`mdx` 也走文档视图，只是组件位置换成占位块。 */
const MARKDOWN_EXTS = new Set(["md", "markdown", "mdx"]);

/** 结构化数据的扩展名：这些渲染成可折叠的树。 */
const DATA_EXTS = new Set(["json", "yaml", "yml"]);

/** 按扩展名分流：markdown 走文档视图，json/yaml 走折叠树，认得的源码走代码视图，其余仍是纯文本。 */
function render(doc: Document, text: string): HTMLElement {
  const ext = extOf(sourceUrl(doc));
  if (MARKDOWN_EXTS.has(ext)) return renderDocument(doc, text);
  if (DATA_EXTS.has(ext)) return renderData(doc, text, ext !== "json");
  if (ext === "csv") return renderCsv(doc, text);
  if (ext === "log") return renderLog(doc, text);
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
 * json / yaml 视图：先挂空壳子，解析和建树在 data 包里异步做完再填进来，`prettify` 仍是同步的。
 *
 * 语法有错时不是一片空白：顶上一条说清第几行错在哪，底下照常是排版好的原文。
 */
function renderData(doc: Document, text: string, yaml: boolean): HTMLElement {
  const host = doc.createElement("div");
  host.className = "lfv-data";
  void fillData(doc, host, text, yaml);
  return host;
}

async function fillData(
  doc: Document,
  host: HTMLElement,
  text: string,
  yaml: boolean,
): Promise<void> {
  const module = await LAZY.data();
  const parsed = module.parseData(text, yaml);

  if (parsed.ok) {
    host.replaceChildren(makeCopyButton(doc, text), module.renderTree(doc, parsed.value));
  } else {
    host.replaceChildren(
      module.renderError(doc, parsed.error),
      renderCode(doc, text, yaml ? "yaml" : "json"),
    );
  }
  host.classList.add("lfv-rendered");
}

/** csv 视图：同样先挂空壳子，切分和建表在 csv 包里异步做完再填进来。 */
function renderCsv(doc: Document, text: string): HTMLElement {
  const host = doc.createElement("div");
  host.className = "lfv-csv";
  void fillCsv(doc, host, text);
  return host;
}

async function fillCsv(doc: Document, host: HTMLElement, text: string): Promise<void> {
  const module = await LAZY.csv();
  host.replaceChildren(
    makeCopyButton(doc, text),
    module.renderTable(doc, module.parseCsv(text)),
  );
  host.classList.add("lfv-rendered");
}

/** 日志视图：壳子先挂上，拆行和上色在 log 包里异步做完再填进来。 */
function renderLog(doc: Document, text: string): HTMLElement {
  const host = doc.createElement("div");
  host.className = "lfv-logs";
  void fillLog(doc, host, text);
  return host;
}

async function fillLog(doc: Document, host: HTMLElement, text: string): Promise<void> {
  const module = await LAZY.log();
  host.replaceChildren(makeCopyButton(doc, text), await module.renderLog(doc, text));
  host.classList.add("lfv-rendered");
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
  const module = await LAZY.markdown();
  const article = module.renderMarkdown(doc, text, extOf(sourceUrl(doc)) === "mdx");
  const toc = module.renderToc(doc, article);
  host.replaceChildren(...(toc === null ? [article] : [toc, article]));

  // 围栏代码块上 marked 已经写好了 `language-xx`，复用同一个高亮包着色。
  // 标了 `mermaid` 的那些不着色，它们要画成图。
  const blocks = article.querySelectorAll<HTMLElement>("pre > code[class*='language-']");
  await Promise.all([
    ...Array.from(blocks, (code) => {
      const language = /language-([\w-]+)/.exec(code.className)?.[1];
      if (language === undefined || language === "mermaid") return Promise.resolve();
      return colorize(code, language);
    }),
    drawDiagrams(doc, article),
    typesetMath(doc, article),
  ]);

  wireLocalLinks(doc, host);
  host.classList.add("lfv-rendered");
  scrollToHash(doc);
}

/** 扩展自己那张展示页（`chrome-extension://…/viewer.html`）。Firefox 那边协议名不同。 */
function isExtensionPage(doc: Document): boolean {
  return /^(chrome|moz)-extension:$/.test(new URL(doc.URL).protocol);
}

/**
 * 文档里指向本地文件的链接。
 *
 * **文件页（`file://`）上一个都不拦**：点 `file://` 链接本来就是浏览器份内的事，新页面
 * 还是 `file://`，内容脚本照样接管并渲染，前进后退也就是浏览器原来那一套。
 *
 * 这里原先会先 `fetch` 探一下文件在不在、不在就给提示。那条路在内容脚本里走不通：内容
 * 脚本的请求用的是所在页面的源，`file://` 是不透明源、响应又没有 CORS 头，请求一律失败，
 * 于是**每一个**本地链接都被判成「文件不存在」，点了打不开——这正是要修的毛病。
 * 出处：<https://www.chromium.org/Home/chromium-security/extension-content-script-fetches/>
 * 代价：链接指向的文件真的不存在时，看到的是浏览器自己的报错页，不再有我们那句提示。
 *
 * **展示页（`chrome-extension://`）上全拦**：Chrome 不让普通页面导航到 `file://`，
 * 点了会毫无反应，所以那边把跳转交给后台脚本用 `chrome.tabs.update` 去做。
 */
function wireLocalLinks(doc: Document, host: HTMLElement): void {
  if (!isExtensionPage(doc)) return;

  host.addEventListener("click", (event) => {
    const mouse = event as MouseEvent;
    // 新标签页打开、中键、右键都交回浏览器。
    if (mouse.defaultPrevented || mouse.button !== 0 || mouse.metaKey || mouse.ctrlKey) return;

    const link = (event.target as Element | null)?.closest?.("a");
    const href = link?.getAttribute("href");
    if (href === null || href === undefined) return;

    // 页内锚点是浏览器的活。展示页上正文的地址是那个本地文件，所以 `#x` 解析出来也是
    // `file:`，不先挡掉就会被当成跳文件。
    if (href.startsWith("#")) return;

    const target = new URL(href, sourceUrl(doc));
    if (target.protocol !== "file:") return;
    if (target.href.split("#")[0] === sourceUrl(doc).split("#")[0]) return;

    event.preventDefault();
    void chrome.runtime.sendMessage({ type: "lfv-open", url: target.href });
  });
}

/**
 * 标了 `mermaid` 的围栏代码块画成图。文档里一个都没有时，绘图包一次都不加载。
 *
 * 画不出来（语法写错）就把原文留在原地，底下补一句错在哪——整篇文档的其余部分照常。
 */
async function drawDiagrams(doc: Document, article: HTMLElement): Promise<void> {
  const blocks = article.querySelectorAll<HTMLElement>("pre > code.language-mermaid");
  if (blocks.length === 0) return;

  const module = await LAZY.mermaid();
  const draw = module.renderDiagram;

  await Promise.all(
    Array.from(blocks, async (code, i) => {
      const box = doc.createElement("div");
      box.className = "lfv-diagram";
      try {
        const svg = await draw(code.textContent ?? "", `lfv-diagram-${i}`);
        box.innerHTML = svg;
        // 全屏预览要的是画出来的 SVG 本体；闭包存字符串，box 里追加的按钮不会混进去。
        const view = doc.createElement("button");
        view.type = "button";
        view.className = "lfv-diagram-view";
        view.textContent = "查看大图";
        view.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openDiagramPreview(doc, svg);
        });
        box.append(view);
      } catch (error) {
        box.classList.add("lfv-diagram-error");
        const source = doc.createElement("pre");
        source.textContent = code.textContent ?? "";
        const why = doc.createElement("p");
        why.textContent = `这张图画不出来：${(error as Error).message}`;
        box.replaceChildren(source, why);
      }
      code.parentElement?.replaceWith(box);
    }),
  );
}

/** `$…$` 排成公式。文档里一个公式都没有时，KaTeX 一次都不加载。 */
async function typesetMath(doc: Document, article: HTMLElement): Promise<void> {
  const nodes = article.querySelectorAll<HTMLElement>(".lfv-math[data-tex]");
  if (nodes.length === 0) return;

  ensureStylesheet(doc, "katex.css", "lfv-katex-style");
  const module = await LAZY.katex();
  for (const node of nodes) {
    module.renderMath(node, node.dataset["tex"] ?? "", node.classList.contains("lfv-math-block"));
  }
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
  const module = await LAZY.highlight();
  const html = module.colorize(code.textContent ?? "", language);
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

function makeButton(
  doc: Document,
  label: string,
  onClick: () => void,
  right = "8px",
): HTMLElement {
  const button = doc.createElement("button");
  button.className = "lfv-toggle";
  button.textContent = label;
  // 内联样式而不是靠样式表：不接管的页面上这个按钮是唯一的改动，不该顺带拖进一张 CSS。
  button.setAttribute(
    "style",
    `position:fixed;top:8px;right:${right};z-index:2147483647;opacity:0.45;` +
      "padding:4px 10px;border-radius:6px;border:1px solid #666;" +
      "background:#1c1c1f;color:#eee;font:12px/1.4 system-ui,sans-serif;cursor:pointer",
  );
  button.addEventListener("click", onClick);
  return button;
}

/** 已经装过查找快捷键的页面。一个页面只装一次，来回切换不重复装。 */
const wired = new WeakSet<Document>();

/**
 * 接管查找快捷键（macOS 的 `Cmd+F`，其余平台的 `Ctrl+F`）。
 *
 * 只在美化档接管：`lfv-on` 不在就什么都不做，浏览器自带的查找照常弹出来。
 * 搜索那套代码单独一个包，第一次真按下去才去加载。
 */
function wireSearch(doc: Document): void {
  if (wired.has(doc)) return;
  wired.add(doc);
  doc.addEventListener("keydown", (event) => {
    const key = event as KeyboardEvent;
    if (key.key !== "f" || !(key.metaKey || key.ctrlKey)) return;
    if (!doc.documentElement.classList.contains("lfv-on")) return;
    key.preventDefault();
    void LAZY.search().then((module) => {
      module.openSearch(doc);
    });
  });
}

const STYLE_ID = "lfv-style";

/** 挂一张扩展自带的样式表。同一个 id 只挂一次；KaTeX 的那张同样走这里，用完才挂。 */
function ensureStylesheet(doc: Document, file = "viewer.css", id = STYLE_ID): void {
  if (doc.getElementById(id)) return;
  const link = doc.createElement("link");
  link.id = id;
  link.setAttribute("rel", "stylesheet");
  link.setAttribute("href", chrome.runtime.getURL(file));
  doc.head.append(link);
}
