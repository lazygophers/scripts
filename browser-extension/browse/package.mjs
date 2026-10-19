/**
 * 把 browse 扩展打包为 .crx，输出到项目根 `dist/`。
 *
 * 用法：`npm run package`
 */
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildExtension } from "../shared/build.mjs";
import { packCrx } from "../shared/package.mjs";
import { options } from "./build.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
const rootDist = join(root, "..", "..", "dist");

await buildExtension({ ...options, root, target: "chrome", sourcemap: false });
const dist = join(root, "dist");

await packCrx({ dist, out: rootDist, name: `browse-${version}.crx`, root });
