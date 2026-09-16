import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

await buildExtension({
  root: dirname(fileURLToPath(import.meta.url)),
  // The content script loads as a classic script, so it is built as an IIFE.
  // The highlight bundle is a separate ESM module the content script imports at
  // runtime, so the highlighter's weight is only paid on code files.
  iifeEntryPoints: ["src/content.ts"],
  entryPoints: ["src/highlight.ts"],
  copy: ["viewer.css"],
  target: process.argv.includes("--firefox") ? "firefox" : "chrome",
  watch: process.argv.includes("--watch"),
});
