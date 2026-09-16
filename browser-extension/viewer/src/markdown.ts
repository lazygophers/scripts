/**
 * markdown 渲染包，和高亮包一样单独打成一个 bundle，只有真的打开 `.md` 时才被动态加载。
 *
 * 这里只负责「文本 -> 干净的 DOM」：解析、净化、front matter 信息表、标题锚点。
 * 围栏代码块的着色不在这里做——marked 已经把语言写进 `<code class="language-go">`，
 * 由 `prettify.ts` 复用它已有的高亮包去上色，省得把 highlight.js 再打进本包一份。
 */

import DOMPurify, { type WindowLike } from "dompurify";
import { Marked, type Tokens } from "marked";

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

  return new Marked({
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

/**
 * 渲染成一个 `<article>`。文档里原有的 HTML 片段会先过一遍 DOMPurify，
 * `<script>`、`onerror=` 这类东西在插进页面之前就被摘掉。
 */
export function renderMarkdown(doc: Document, text: string): HTMLElement {
  const { meta, body } = splitFrontMatter(text);
  const html = DOMPurify(doc.defaultView as unknown as WindowLike).sanitize(
    parser().parse(body, { async: false }),
  );

  const article = doc.createElement("article");
  article.className = "lfv-md";
  article.innerHTML = html;
  if (meta.length > 0) article.prepend(metaTable(doc, meta));
  return article;
}
