import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

export const options = {
  entryPoints: ["src/background.ts", "src/confirm-page.ts", "src/panel.ts", "src/settings.ts", "src/offscreen-doc.ts", "src/picker.ts"],
  iifeEntryPoints: ["src/page-locate.ts"],
  copy: [
    "confirm.html",
    "panel.html",
    "settings.html",
    "offscreen.html",
    "picker.html",
    "ui.css",
    "assets",
    "_locales",
  ],
  target: process.argv.includes("--firefox") ? "firefox" : "chrome",
  watch: process.argv.includes("--watch"),
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await buildExtension({ ...options, root: dirname(fileURLToPath(import.meta.url)) });
}
