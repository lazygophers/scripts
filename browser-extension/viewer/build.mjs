import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

/**
 * 构建配置单独摆出来，`package.mjs` 打两个浏览器的包时照用同一份。
 *
 * The content script loads as a classic script, so it is built as an IIFE.
 * The highlight bundle is a separate ESM module the content script imports at
 * runtime, so the highlighter's weight is only paid on code files.
 */
export const options = {
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
    "src/background.ts",
    "src/settings-page.ts",
    "src/viewer-page.ts",
  ],
  copy: ["viewer.css", "settings.html", "viewer.html"],
  // KaTeX 的样式表要带着它自己的字体走：`url()` 交给 esbuild 改写成 dist 里的文件，
  // 页面上因此不会去网络取字体（扩展的内容安全策略也不允许）。
  loader: { ".woff": "file", ".woff2": "file", ".ttf": "file" },
};

// 直接跑 `node build.mjs` 才真的构建；被 `package.mjs` import 时只取上面那份配置。
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await buildExtension({
    ...options,
    root: dirname(fileURLToPath(import.meta.url)),
    target: process.argv.includes("--firefox") ? "firefox" : "chrome",
    watch: process.argv.includes("--watch"),
  });
}
