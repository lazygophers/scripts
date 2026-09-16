import { Readability } from "@mozilla/readability";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

export interface PageSnapshot {
  title: string;
  url: string;
  selectionHtml: string;
  documentHtml: string;
}

export interface MarkdownOptions {
  includeTitle: boolean;
  includeSource: boolean;
}

export const DEFAULT_MARKDOWN_OPTIONS: MarkdownOptions = { includeTitle: true, includeSource: true };

export function collectPageSnapshot(): PageSnapshot {
  const selection = window.getSelection();
  let selectionHtml = "";

  if (selection && !selection.isCollapsed && selection.toString().trim()) {
    const container = document.createElement("div");
    for (let index = 0; index < selection.rangeCount; index += 1) {
      container.append(selection.getRangeAt(index).cloneContents());
    }
    selectionHtml = container.innerHTML;
  }

  return {
    title: document.title,
    url: window.location.href,
    selectionHtml,
    documentHtml: document.documentElement.outerHTML,
  };
}

function absoluteContent(html: string, sourceUrl: string, parser: DOMParser): string {
  const document = parser.parseFromString(`<body>${html}</body>`, "text/html");
  for (const [selector, attribute] of [
    ["a[href]", "href"],
    ["img[src]", "src"],
  ] as const) {
    for (const element of document.querySelectorAll<HTMLElement>(selector)) {
      const value = element.getAttribute(attribute);
      if (!value) continue;
      try {
        element.setAttribute(attribute, new URL(value, sourceUrl).href);
      } catch {
        // Keep malformed page URLs unchanged; Turndown can still preserve their text.
      }
    }
  }
  return document.body.innerHTML;
}

export function markdownFromSnapshot(
  snapshot: PageSnapshot,
  parser = new DOMParser(),
  options: MarkdownOptions = DEFAULT_MARKDOWN_OPTIONS,
): string {
  const sourceDocument = parser.parseFromString(snapshot.documentHtml, "text/html");
  let html = snapshot.selectionHtml;

  if (!html.trim()) {
    try {
      html = new Readability(sourceDocument.cloneNode(true) as Document).parse()?.content ?? "";
    } catch {
      html = "";
    }
    if (!html.trim()) html = sourceDocument.body?.innerHTML ?? "";
  }

  const turndown = new TurndownService({ bulletListMarker: "-", headingStyle: "atx" });
  turndown.use(gfm);
  // 本版本的 turndown-plugin-gfm 没有 fencedCodeBlock（只有 highlight-xxx 的 DIV 规则），
  // <pre><code> 会掉进默认的 4 空格缩进规则：首行缩进被吞、language-* 丢失。自己补围栏。
  turndown.addRule("fencedCodeBlock", {
    filter: (node) => node.nodeName === "PRE" && node.firstElementChild?.nodeName === "CODE",
    replacement: (_content, node) => {
      const code = node.firstElementChild as HTMLElement;
      const language = /(?:^|\s)language-([\w-]+)/u.exec(code.className)?.[1] ?? "";
      const text = (code.textContent ?? "").replace(/\n$/u, "");
      return `\n\n\`\`\`${language}\n${text}\n\`\`\`\n\n`;
    },
  });
  const body = turndown.turndown(absoluteContent(html, snapshot.url, parser)).trim();
  if (!body) throw new Error("页面没有可导出的文字内容");

  const header: string[] = [];
  if (options.includeTitle) header.push(`# ${snapshot.title.replace(/\s+/gu, " ").trim() || "未命名网页"}`);
  if (options.includeSource) header.push(`来源：<${snapshot.url}>`);
  return [...header, body].join("\n\n") + "\n";
}
