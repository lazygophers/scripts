import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

await buildExtension({
  root: dirname(fileURLToPath(import.meta.url)),
  // The only script is the content script, and content scripts load as classic
  // scripts — there is no ESM entry point here.
  iifeEntryPoints: ["src/content.ts"],
  copy: ["viewer.css"],
  target: process.argv.includes("--firefox") ? "firefox" : "chrome",
  watch: process.argv.includes("--watch"),
});
