/**
 * 一条命令产出两个浏览器的安装包：`npm run package`。
 *
 * 构建配置和 `build.mjs` 共用一份（`options`），只是跑两遍、目标不同，
 * 所以 Chrome 版和 Firefox 版永远是同一份源码、同一份 manifest 生成的。
 */
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";
import { packDist } from "../shared/package.mjs";
import { options } from "./build.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
const out = join(root, "dist-package");

for (const target of ["chrome", "firefox"]) {
  await buildExtension({ ...options, root, target, sourcemap: false });
  const dist = join(root, target === "chrome" ? "dist" : `dist-${target}`);
  const zip = await packDist({ dist, out, name: `viewer-${version}-${target}.zip` });
  console.log(`${target} -> ${zip}`);
}
