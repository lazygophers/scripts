import assert from "node:assert/strict";
import test from "node:test";
import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { buildExtension, watchers } from "../build.mjs";

/**
 * build.mjs 的主路径真跑：esbuild 从 shared 自己的 node_modules 解析；
 * tsc 是 spawnSync 的外部命令，用 stub 站住「类型检查通过/不过」两支，
 * 不依赖任何真实 TypeScript 项目。
 */
async function scaffold(tscExit = 0, withTsc = true) {
  const root = await mkdtemp(join(tmpdir(), "shared-build-"));
  await mkdir(join(root, "src"), { recursive: true });
  if (withTsc) await mkdir(join(root, "node_modules", ".bin"), { recursive: true });
  await writeFile(join(root, "src", "manifest.json"), JSON.stringify({
    manifest_version: 3, name: "x", version: "0.0.1",
  }));
  await writeFile(join(root, "src", "sw.ts"), "console.log(1);\n");
  await writeFile(join(root, "src", "inject.ts"), "console.log(2);\n");
  await writeFile(join(root, "src", "extra.css"), "body{}\n");
  if (withTsc) {
    const tsc = join(root, "node_modules", ".bin", "tsc");
    await writeFile(tsc, `#!/bin/sh\nexit ${tscExit}\n`);
    await chmod(tsc, 0o755);
  }
  return root;
}

test("buildExtension：esm+iife 都打、copy、manifest 与 palette 落 dist", { timeout: 60_000 }, async () => {
  const root = await scaffold();
  try {
    await buildExtension({
      root,
      entryPoints: ["src/sw.ts"],
      iifeEntryPoints: ["src/inject.ts"],
      copy: ["extra.css"],
      sourcemap: false,
    });
    for (const f of ["dist/sw.js", "dist/inject.js", "dist/extra.css", "dist/palette.css"]) {
      assert.equal(existsSync(join(root, f)), true, `${f} 没打出来`);
    }
    const manifest = JSON.parse(await (await import("node:fs/promises")).readFile(join(root, "dist", "manifest.json"), "utf8"));
    assert.equal(manifest.manifest_version, 3);
  } finally {
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});

test("buildExtension：firefox 目标写进 dist-firefox", { timeout: 60_000 }, async () => {
  const root = await scaffold();
  try {
    await buildExtension({ root, entryPoints: ["src/sw.ts"], target: "firefox", sourcemap: false });
    assert.equal(existsSync(join(root, "dist-firefox", "sw.js")), true, "firefox dist 没打出来");
  } finally {
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});

test("buildExtension：tsc 不过就 process.exit，不带病出包", { timeout: 60_000 }, async () => {
  const root = await scaffold(1);
  const exits = [];
  const realExit = process.exit;
  process.exit = (code) => { exits.push(code); throw new Error("exit-called"); };
  try {
    await assert.rejects(
      () => buildExtension({ root, entryPoints: ["src/sw.ts"], sourcemap: false }),
      /exit-called/,
    );
    assert.deepEqual(exits, [1]);
  } finally {
    process.exit = realExit;
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});

test("buildExtension：tsc 不在（ENOENT）也是退，退出码缺省 1", { timeout: 60_000 }, async () => {
  const root = await scaffold(0, false);
  const exits = [];
  const realExit = process.exit;
  process.exit = (code) => { exits.push(code ?? null); throw new Error("exit-called"); };
  try {
    await assert.rejects(
      () => buildExtension({ root, entryPoints: ["src/sw.ts"], sourcemap: false }),
      /exit-called/,
    );
    assert.deepEqual(exits, [1]); // tsc.error 真值时 status 为 null，`?? 1` 落到 1
  } finally {
    process.exit = realExit;
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});

test("buildExtension：watch 模式建 context 并可收走", { timeout: 60_000 }, async () => {
  const root = await scaffold();
  try {
    await buildExtension({ root, entryPoints: ["src/sw.ts"], iifeEntryPoints: ["src/inject.ts"], watch: true, sourcemap: false });
    assert.equal(watchers.length >= 2, true, "esm/iife 两个 context 都该建出来");
    assert.equal(existsSync(join(root, "dist", "manifest.json")), true, "watch 模式也写 manifest");
    for (const ctx of watchers.splice(0)) await ctx.dispose();
  } finally {
    for (const ctx of watchers.splice(0)) await ctx.dispose();
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});
