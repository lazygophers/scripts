import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const watch = process.argv.includes("--watch");
const outdir = "dist";

// esbuild 不做类型检查——没这道闸，类型错误只有读代码的人看得见（2026-09-15 之前
// typecheck 还带着一个既有错误，信号彻底没人看）。watch 模式不加：增量重载不该
// 被一个旧错误的失败卡住。
if (!watch) {
  const tsc = spawnSync("node_modules/.bin/tsc", ["--noEmit"], { stdio: "inherit" });
  if (tsc.error || tsc.status !== 0) {
    process.exit(tsc.status ?? 1);
  }
}

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

const options = {
  // The two extension pages ship as ESM like the worker: `confirm.html` is the
  // spec 4.4 dialog, `panel.html` the spec 4.5 toolbar panel.
  entryPoints: ["src/background.ts", "src/confirm-page.ts", "src/panel.ts", "src/settings.ts"],
  outdir,
  bundle: true,
  format: "esm",
  target: "chrome116",
  sourcemap: true,
  logLevel: "info",
};

// Injected with executeScript({ files }), which loads a classic script, not a
// module — hence iife and a separate build from the ESM service worker.
const pageOptions = {
  entryPoints: ["src/page-locate.ts"],
  outdir,
  bundle: true,
  format: "iife",
  target: "chrome116",
  sourcemap: true,
  logLevel: "info",
};

if (watch) {
  await (await context(options)).watch();
  await (await context(pageOptions)).watch();
} else {
  await build(options);
  await build(pageOptions);
}

for (const file of ["manifest.json", "confirm.html", "panel.html", "settings.html", "ui.css"]) {
  await cp(`src/${file}`, `${outdir}/${file}`);
  console.log(`${file} -> ${outdir}/${file}`);
}

// `_locales` must sit at the extension root or `__MSG_*__` in the manifest
// resolves to nothing and Chrome refuses to load the extension.
await cp("src/_locales", `${outdir}/_locales`, { recursive: true });
console.log(`_locales -> ${outdir}/_locales`);
