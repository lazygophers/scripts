import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const watch = process.argv.includes("--watch");
const outdir = "dist";

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

const options = {
  entryPoints: ["src/background.ts"],
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

await cp("src/manifest.json", `${outdir}/manifest.json`);
console.log(`manifest.json -> ${outdir}/manifest.json`);
