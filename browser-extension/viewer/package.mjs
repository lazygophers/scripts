/**
 * 一条命令产出两个浏览器的安装包：`npm run package`。
 *
 * 构建配置和 `build.mjs` 共用一份（`options`），只是跑两遍、目标不同，
 * 所以 Chrome 版和 Firefox 版永远是同一份源码、同一份 manifest 生成的。
 *
 * 打包产物输出到项目根 `dist/`：两个浏览器各一个 .zip，Chrome 另出一个 .crx。
 */
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";
import { packCrx, packDist } from "../shared/package.mjs";
import { options } from "./build.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
const rootDist = join(root, "..", "..", "dist");

for (const target of ["chrome", "firefox"]) {
  await buildExtension({ ...options, root, target, sourcemap: false });
  const dist = join(root, target === "chrome" ? "dist" : `dist-${target}`);
  const zip = await packDist({ dist, out: rootDist, name: `viewer-${version}-${target}.zip` });
  console.log(`${target} -> ${zip}`);
  // Mac/Windows 的 Chrome 拒装本地 .crx（只认应用商店），crx 只给 Linux 或企业策略用；平时装 zip 解压后的目录
  if (target === "chrome") await packCrx({ dist, out: rootDist, name: `viewer-${version}.crx`, root });
}
