import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

await buildExtension({
  root: dirname(fileURLToPath(import.meta.url)),
  // The two extension pages ship as ESM like the worker: `confirm.html` is the
  // spec 4.4 dialog, `panel.html` the spec 4.5 toolbar panel. `offscreen.html`
  // hosts the DOM the SW lacks (recorder + clipboard), `picker.html` is the
  // user-gesture page desktopCapture needs.
  entryPoints: ["src/background.ts", "src/confirm-page.ts", "src/panel.ts", "src/settings.ts", "src/offscreen-doc.ts", "src/picker.ts"],
  iifeEntryPoints: ["src/page-locate.ts"],
  copy: [
    "confirm.html",
    "panel.html",
    "settings.html",
    "offscreen.html",
    "picker.html",
    "ui.css",
    // 通知和工具栏图标要用（notifications.create 的 iconUrl 必须指向包内文件）。
    "assets",
    // `_locales` must sit at the extension root or `__MSG_*__` in the manifest
    // resolves to nothing and Chrome refuses to load the extension.
    "_locales",
  ],
  target: process.argv.includes("--firefox") ? "firefox" : "chrome",
  watch: process.argv.includes("--watch"),
});
