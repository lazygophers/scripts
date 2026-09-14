import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const watch = process.argv.includes("--watch");
const outdir = "dist";

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
