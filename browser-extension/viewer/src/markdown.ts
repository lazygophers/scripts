/**
 * markdown 渲染包，和高亮包一样单独打成一个 bundle，只有真的打开 `.md` 时才被动态加载。
 *
 * 这里只负责「文本 -> 干净的 DOM」：解析、净化、front matter 信息表、标题锚点。
 * 围栏代码块的着色不在这里做——marked 已经把语言写进 `<code class="language-go">`，
 * 由 `prettify.ts` 复用它已有的高亮包去上色，省得把 highlight.js 再打进本包一份。
 */

import DOMPurify, { type WindowLike } from "dompurify";
import { Marked, type Tokens } from "marked";

import { DIALECTS } from "./dialects.ts";

/** front matter：文件开头用三根横线围起来的一段元信息。 */
const FRONT_MATTER = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/;

/**
 * 拆出 front matter 和正文。
 *
 * ponytail: 只认一层 `key: value`，嵌套结构和数组原样当字符串显示；
 * 真需要完整 YAML 时再引 parser，现在为了一张信息表不值得多背一个依赖。
 */
function splitFrontMatter(text: string): { meta: [string, string][]; body: string } {
  const matched = FRONT_MATTER.exec(text);
  if (!matched) return { meta: [], body: text };

  const meta: [string, string][] = [];
  for (const line of (matched[1] ?? "").split(/\r?\n/)) {
    const colon = line.indexOf(":");
    if (colon <= 0) continue;
    meta.push([line.slice(0, colon).trim(), line.slice(colon + 1).trim()]);
  }
  return { meta, body: text.slice(matched[0].length) };
}

/** 标题文字转成地址里那一截锚点名，和 GitHub 的规则一致：小写、去标点、空格换连字符。 */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s+/g, "-");
}

/**
 * 每次渲染新开一个 marked 实例，因为「同名标题加序号」的计数器是一次渲染内的状态。
 *
 * 只改了标题渲染：加上 `id` 和一个可点的锚点。其余语法走 marked 自带的 GFM 输出。
 */
function parser(): Marked {
  const used = new Map<string, number>();

  return new Marked(...DIALECTS, {
    gfm: true,
    renderer: {
      heading(this: { parser: { parseInline: (t: Tokens.Generic[]) => string } }, token: Tokens.Heading) {
        const inner = this.parser.parseInline(token.tokens);
        const base = slugify(token.text) || "section";
        const seen = used.get(base) ?? 0;
        used.set(base, seen + 1);
        const id = seen === 0 ? base : `${base}-${seen}`;
        return (
          `<h${token.depth} id="${id}">${inner}` +
          `<a class="lfv-anchor" href="#${id}" aria-label="本节链接">#</a>` +
          `</h${token.depth}>\n`
        );
      },
    },
  });
}

/** MDX 里的 `import` / `export` 行，和正文无关，删掉。 */
const MDX_MODULE = /^(?:import|export)\s[^\n]*\n?/gm;

/** 顶格写的 React 组件，自闭合或成对都算。小写开头的是普通 HTML 标签，不动。 */
const MDX_COMPONENT = /^<([A-Z][\w.]*)(?:[^>]*\/>|[\s\S]*?<\/\1>)[^\S\n]*$/gm;

/**
 * `.mdx` 的降级：正文按普通 markdown 渲染，组件位置留一块写清楚的占位。
 *
 * 这是硬约束不是偷懒：扩展的内容安全策略禁止运行临时生成的代码
 * （<https://developer.chrome.com/docs/extensions/reference/manifest/content-security-policy>），
 * 而 MDX 必须先编译成 JavaScript 再执行，所以组件在这里没法真的跑起来。
 */
export function degradeMdx(text: string): string {
  return text.replace(MDX_MODULE, "").replace(
    MDX_COMPONENT,
    (_whole, name: string) =>
      `<div class="lfv-mdx">这里本来有一个 <code>${name}</code> 组件。` +
      `扩展不允许运行临时生成的代码，组件在这里跑不起来，其余内容照常显示。</div>`,
  );
}

/** front matter 的信息表：键在左，值在右。 */
function metaTable(doc: Document, meta: [string, string][]): HTMLElement {
  const table = doc.createElement("table");
  table.className = "lfv-front-matter";
  for (const [key, value] of meta) {
    const row = doc.createElement("tr");
    const name = doc.createElement("th");
    name.textContent = key;
    const cell = doc.createElement("td");
    cell.textContent = value;
    row.append(name, cell);
    table.append(row);
  }
  return table;
}

/** 侧边栏放不下的窗口宽度，和 `viewer.css` 里那条 `@media (max-width: 60rem)` 是同一个界。 */
const NARROW = 960;

/**
 * 目录：把正文里的标题按层级列成一条侧边栏，没有标题就返回 null（页面上不留空侧栏）。
 *
 * 用 `<details>` 而不是自己写展开逻辑：窄窗口下它自带点开收起，宽窗口由 CSS 强制摊开。
 * 跳转也不写 JS，`<a href="#id">` 本来就是浏览器的活。
 */
export function renderToc(doc: Document, article: HTMLElement): HTMLElement | null {
  const heads = Array.from(article.querySelectorAll<HTMLElement>("h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]"));
  if (heads.length === 0) return null;

  const toc = doc.createElement("details");
  toc.className = "lfv-toc";
  // 宽屏摊开（CSS 把「目录」那个开关藏掉），窄屏收成一行，点开才占地方。
  toc.open = (doc.defaultView?.innerWidth ?? 0) > NARROW;
  const title = doc.createElement("summary");
  title.textContent = "目录";
  toc.append(title);

  const links = heads.map((head) => {
    const link = doc.createElement("a");
    link.href = `#${head.id}`;
    // 标题里那个锚点「#」不该出现在目录里。
    link.textContent = head.textContent?.replace(/#$/, "") ?? "";
    link.dataset["level"] = head.tagName.slice(1);
    toc.append(link);
    return link;
  });

  spy(doc, heads, links);
  return toc;
}

/**
 * 滚动时高亮当前所在的那一节：取最后一个已经滚过视口顶部的标题。
 *
 * ponytail: 直接读 `getBoundingClientRect()`，标题多到几百条时每次滚动都要量一遍；
 * 真遇到卡顿再换 IntersectionObserver。
 */
function spy(doc: Document, heads: HTMLElement[], links: HTMLElement[]): void {
  const update = () => {
    let active = 0;
    heads.forEach((head, i) => {
      if (head.getBoundingClientRect().top <= 80) active = i;
    });
    links.forEach((link, i) => link.classList.toggle("lfv-active", i === active));
  };
  doc.defaultView?.addEventListener("scroll", update, { passive: true });
  update();
}

/**
 * 放行的地址协议。DOMPurify 自带的那张表里**没有 `file:`**，于是文档里写成
 * `file:///…` 的绝对链接会被摘掉 `href`、图片会被摘掉 `src`——渲染出来是个点不动的
 * 光秃秃 `<a>`。本扩展干的就是看本地文件的活，这个协议必须放行。
 *
 * 这里是抄它 v3 的默认表再加一个 `file`，别的一个字没改：`javascript:` / `data:`
 * 仍然挡着。出处：`node_modules/dompurify/dist/purify.cjs.js` 的 `IS_ALLOWED_URI`。
 */
const ALLOWED_URI =
  /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|matrix|file):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i;

/**
 * 渲染成一个 `<article>`。文档里原有的 HTML 片段会先过一遍 DOMPurify，
 * `<script>`、`onerror=` 这类东西在插进页面之前就被摘掉。
 */
export function renderMarkdown(doc: Document, text: string, mdx = false): HTMLElement {
  const { meta, body } = splitFrontMatter(mdx ? degradeMdx(text) : text);
  const html = DOMPurify(doc.defaultView as unknown as WindowLike).sanitize(
    parser().parse(body, { async: false }),
    { ALLOWED_URI_REGEXP: ALLOWED_URI },
  );

  const article = doc.createElement("article");
  article.className = "lfv-md";
  article.innerHTML = html;
  if (meta.length > 0) article.prepend(metaTable(doc, meta));
  return article;
}
