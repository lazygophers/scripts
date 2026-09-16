import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";

await buildExtension({
  root: dirname(fileURLToPath(import.meta.url)),
  entryPoints: ["src/popup.ts", "src/options.ts"],
  copy: ["popup.html", "options.html", "ui.css"],
});
