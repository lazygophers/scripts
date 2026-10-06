import { strictEqual } from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * `node build.mjs` 的主守卫（`process.argv[1] === 本文件`）在进程内伪装 argv 触发，
 * 这样覆盖率归因得到（spawnSync 子进程测不到），效果等于真跑一次构建。
 */
async function runBuild(extra: string[] = []): Promise<void> {
  const self = fileURLToPath(new URL("../build.mjs", import.meta.url));
  const realArgv = process.argv;
  process.argv = [realArgv[0]!, self, ...extra];
  try {
    await import(`${self}?${Math.random()}`); // query 破缓存让守卫每次重新求值
  } finally {
    process.argv = realArgv;
  }
}

test("build.mjs 真跑：chrome/firefox 两版 dist 与 manifest 都出来", { timeout: 120_000 }, async () => {
  await runBuild();
  await runBuild(["--firefox"]);

  const dist = join(ROOT, "dist");
  strictEqual(existsSync(join(dist, "manifest.json")), true, "chrome dist 缺 manifest");
  const manifest = JSON.parse(readFileSync(join(dist, "manifest.json"), "utf8")) as {
    manifest_version: number;
  };
  strictEqual(manifest.manifest_version, 3);
  strictEqual(existsSync(join(dist, "content.js")), true, "iife 入口没打出来");

  const ff = JSON.parse(readFileSync(join(ROOT, "dist-firefox", "manifest.json"), "utf8")) as {
    background?: { scripts?: string[] };
    key?: unknown;
  };
  strictEqual(Array.isArray(ff.background?.scripts), true, "firefox manifest 没换 background 形状");
  strictEqual("key" in ff, false, "firefox manifest 不该带 Chrome 专有 key");
});
