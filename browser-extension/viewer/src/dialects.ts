/**
 * markdown 的各路方言。每一种都是一个 marked 扩展：语法认得出来就渲染成结构化的 HTML，
 * 认不出来 marked 自己会把那一段当普通文本走，所以写错的语法不会把整篇文档带崩。
 *
 * 脚注和三冒号提示框用现成的包（`marked-footnote` / `marked-directive`），
 * 只有双方括号链接和 Pandoc 的上下标、定义列表是自己写的小 tokenizer——它们各自也就几行。
 */

import { Lexer, type MarkedExtension, type Tokens } from "marked";
import { createDirectives } from "marked-directive";
import markedFootnote from "marked-footnote";

/** 提示框的类型，决定左边那道色条。表里没有的名字按 note 处理。 */
const NOTE_KINDS = new Set(["note", "tip", "info", "warning", "caution", "danger"]);

/** 链接文本里的 HTML 字符，转义了再拼进去。 */
function escapeHtml(text: string): string {
  return text.replace(/[&<>"]/g, (c) => `&${{ "&": "amp", "<": "lt", ">": "gt", '"': "quot" }[c]};`);
}

/**
 * Obsidian 的双方括号页面链接：`[[页面]]` 或 `[[页面|显示成这样]]`。
 *
 * 目标没写扩展名时补一个 `.md`——笔记库里就是这么互相指的。
 */
const wikilink: MarkedExtension = {
  extensions: [
    {
      name: "wikilink",
      level: "inline",
      start: (src: string) => src.indexOf("[["),
      tokenizer(src: string) {
        const matched = /^\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]/.exec(src);
        if (!matched) return undefined;
        const target = (matched[1] ?? "").trim();
        return {
          type: "wikilink",
          raw: matched[0],
          href: /\.\w+$/.test(target) ? target : `${target}.md`,
          text: (matched[2] ?? target).trim(),
        };
      },
      renderer(token: Tokens.Generic) {
        return `<a class="lfv-wikilink" href="${encodeURI(String(token["href"]))}">${escapeHtml(String(token["text"]))}</a>`;
      },
    },
  ],
};

/** Pandoc 的上标 `^x^` 和下标 `~x~`。GFM 的删除线是 `~~x~~`，两个波浪线的先被 marked 吃掉，不冲突。 */
const scripts: MarkedExtension = {
  extensions: (
    [
      ["superscript", "sup", /^\^([^\s^]+)\^/],
      ["subscript", "sub", /^~([^\s~]+)~/],
    ] as const
  ).map(([name, tag, pattern]) => ({
    name,
    level: "inline" as const,
    start: (src: string) => src.search(name === "superscript" ? /\^/ : /~/),
    tokenizer(src: string) {
      const matched = pattern.exec(src);
      if (!matched) return undefined;
      return { type: name, raw: matched[0], text: matched[1] ?? "" };
    },
    renderer(token: Tokens.Generic) {
      return `<${tag}>${escapeHtml(String(token["text"]))}</${tag}>`;
    },
  })),
};

/**
 * Pandoc 的定义列表：一行术语，下面跟着若干行以冒号开头的解释。
 *
 * ponytail: 只认单个术语配单条解释的常见写法，一个术语挂多条解释时后面几条各自成表；
 * 学术文档里这种写法少见，真碰上了再补。
 */
const definitionList: MarkedExtension = {
  extensions: [
    {
      name: "definitionList",
      level: "block",
      start: (src: string) => src.search(/^[^\n]+\n: /m),
      tokenizer(src: string) {
        const matched = /^([^\n]+)\n((?:: [^\n]*\n?)+)/.exec(src);
        if (!matched) return undefined;
        return {
          type: "definitionList",
          raw: matched[0],
          term: matched[1] ?? "",
          details: (matched[2] ?? "")
            .split("\n")
            .filter((line) => line.startsWith(": "))
            .map((line) => line.slice(2)),
        };
      },
      renderer(token: Tokens.Generic) {
        const details = (token["details"] as string[]).map((d) => `<dd>${escapeHtml(d)}</dd>`).join("");
        return `<dl><dt>${escapeHtml(String(token["term"]))}</dt>${details}</dl>`;
      },
    },
  ],
};

/**
 * 三个冒号围起来的提示框，同时也是 Pandoc 的 fenced div：
 *
 * ```
 * :::warning
 * 小心
 * :::
 * ```
 */
const admonition: MarkedExtension = createDirectives([
  {
    level: "container",
    marker: ":::",
    renderer(token) {
      const name = token.meta.name ?? "note";
      const kind = NOTE_KINDS.has(name) ? name : "note";
      return `<div class="lfv-note lfv-note-${kind}">${this.parser.parse(Lexer.lex(token.text))}</div>`;
    },
  },
]);

/**
 * 数学公式：`$x^2$` 行内，`$$…$$` 独立成行。
 *
 * 这里只把公式原文挑出来放进 `data-tex`，排版留给 `prettify.ts` 那边按需加载 KaTeX 去做——
 * 文档里一个公式都没有时，KaTeX 就一次都不会被加载。节点里先摆原始文本：
 * 万一 KaTeX 没能加载上，看到的是 `$x^2$` 而不是一片空白。
 *
 * `$` 后面紧跟空格的不算公式（`价格 $ 5`），美元金额因此不会被误认。
 */
const math: MarkedExtension = {
  extensions: (
    [
      ["mathBlock", "block", /^\$\$([\s\S]+?)\$\$/, /\$\$/],
      ["mathInline", "inline", /^\$(?!\s)((?:[^$\n]|\\\$)+?)(?<!\s)\$/, /\$/],
    ] as const
  ).map(([name, level, pattern, start]) => ({
    name,
    level: level as "block" | "inline",
    start: (src: string) => src.search(start),
    tokenizer(src: string) {
      const matched = pattern.exec(src);
      if (!matched) return undefined;
      return { type: name, raw: matched[0], text: (matched[1] ?? "").trim() };
    },
    renderer(token: Tokens.Generic) {
      const tex = String(token["text"]);
      const [tag, extra] = name === "mathBlock" ? ["div", " lfv-math-block"] : ["span", ""];
      return (
        `<${tag} class="lfv-math${extra}" data-tex="${escapeHtml(tex)}">` +
        `${escapeHtml(name === "mathBlock" ? `$$${tex}$$` : `$${tex}$`)}</${tag}>`
      );
    },
  })),
};

/** 全部方言，直接摊给 `new Marked(...)`。 */
export const DIALECTS: MarkedExtension[] = [
  wikilink,
  scripts,
  math,
  definitionList,
  admonition,
  markedFootnote({ refMarkers: true }),
];
