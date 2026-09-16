import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

await buildExtension({
  root: dirname(fileURLToPath(import.meta.url)),
  // The content script loads as a classic script, so it is built as an IIFE.
  // The highlight bundle is a separate ESM module the content script imports at
  // runtime, so the highlighter's weight is only paid on code files.
  iifeEntryPoints: ["src/content.ts"],
  entryPoints: [
    "src/highlight.ts",
    "src/markdown.ts",
    "src/mermaid.ts",
    "src/katex.ts",
    "src/data.ts",
    "src/csv.ts",
    "src/log.ts",
    "src/listing.ts",
    "src/search.ts",
  ],
  copy: ["viewer.css"],
  // KaTeX 的样式表要带着它自己的字体走：`url()` 交给 esbuild 改写成 dist 里的文件，
  // 页面上因此不会去网络取字体（扩展的内容安全策略也不允许）。
  loader: { ".woff": "file", ".woff2": "file", ".ttf": "file" },
  target: process.argv.includes("--firefox") ? "firefox" : "chrome",
  watch: process.argv.includes("--watch"),
});
