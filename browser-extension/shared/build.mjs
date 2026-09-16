import { build, context } from "esbuild";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { manifestFor } from "./manifest.mjs";

const SHARED = dirname(fileURLToPath(import.meta.url));

/**
 * Build one extension directory into a loadable `dist`.
 *
 * `root` is the extension directory (`browser-extension/<name>`); every other
 * path is relative to `root/src`. `target` picks the manifest flavour, and the
 * two browsers get separate output directories so one command can produce both.
 */
export async function buildExtension({
  root,
  entryPoints = [],
  iifeEntryPoints = [],
  copy = [],
  target = "chrome",
  watch = false,
  outdir = target === "chrome" ? "dist" : `dist-${target}`,
}) {
  const out = join(root, outdir);

  // esbuild 不做类型检查——没这道闸，类型错误只有读代码的人看得见。watch 模式不加：
  // 增量重载不该被一个旧错误的失败卡住。
  if (!watch) {
    const tsc = spawnSync(join(root, "node_modules/.bin/tsc"), ["--noEmit"], {
      cwd: root,
      stdio: "inherit",
    });
    if (tsc.error || tsc.status !== 0) {
      process.exit(tsc.status ?? 1);
    }
  }

  await rm(out, { recursive: true, force: true });
  await mkdir(out, { recursive: true });

  const common = {
    absWorkingDir: root,
    outdir: out,
    bundle: true,
    target: "chrome116",
    sourcemap: true,
    logLevel: "info",
  };
  const esm = { ...common, entryPoints, format: "esm" };
  const hasEsm = entryPoints.length > 0;
  // Injected with executeScript({ files }), which loads a classic script, not a
  // module — hence iife and a separate build from the ESM service worker.
  const iife = { ...common, entryPoints: iifeEntryPoints, format: "iife" };

  if (watch) {
    if (hasEsm) await (await context(esm)).watch();
    if (iifeEntryPoints.length) await (await context(iife)).watch();
  } else {
    if (hasEsm) await build(esm);
    if (iifeEntryPoints.length) await build(iife);
  }

  const manifest = JSON.parse(await readFile(join(root, "src/manifest.json"), "utf8"));
  await writeFile(
    join(out, "manifest.json"),
    `${JSON.stringify(manifestFor(target, manifest), null, 2)}\n`,
  );
  console.log(`manifest.json (${target}) -> ${outdir}/manifest.json`);

  // 深色色板只有一份，两个扩展都从 shared 取，页面里 `ui.css` 直接 @import 它。
  await cp(join(SHARED, "palette.css"), join(out, "palette.css"));
  console.log(`palette.css -> ${outdir}/palette.css`);

  for (const entry of copy) {
    await cp(join(root, "src", entry), join(out, entry), { recursive: true });
    console.log(`${entry} -> ${outdir}/${entry}`);
  }
}
