import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { readFileSync } from "node:fs";

import { build } from "esbuild";

/**
 * 性能守卫：内容脚本的体积。
 *
 * 内容脚本匹配 `<all_urls>`，**每一张网页**都会拉一遍它的 bundle。高亮库、
 * KaTeX、mermaid 因此被拆成按需加载的独立包（见 build.mjs 的 entryPoints 分工）——
 * 谁不小心在 content.ts 的导入链上加了其中一个，所有网页都要替代码文件付钱，
 * 而这种回归在功能测试里一点声音都没有。
 *
 * 用体积而不是「包名有没有出现」来判：`prettify.ts` 里本来就写着
 * `chrome.runtime.getURL("katex.js")` 这种字符串，按名字找会误报；而真把库
 * 打进来体积会翻十倍，预算一眼就拦住。
 *
 * 这里按真实构建参数打一份到内存里量，不依赖 dist 存在。
 */

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** 构建配置按文本读：`build.mjs` 没有类型声明，import 进来过不了 tsc。 */
const BUILD_CONFIG = readFileSync(join(ROOT, "build.mjs"), "utf8");

/** 内容脚本的上限。实测 15 KB；留到 32 KB——高亮库一个人就 160 KB，拖进来一定撞线。 */
const CONTENT_BUDGET_BYTES = 32 * 1024;

/** 按需加载的包各自的上限（括号里是实测值）。同样只拦量级。 */
const LAZY_BUDGET_BYTES: Record<string, number> = {
  "src/markdown.ts": 160 * 1024, // 95 KB
  "src/data.ts": 160 * 1024, // 98 KB
  "src/listing.ts": 12 * 1024, // 4 KB
  "src/csv.ts": 8 * 1024, // 2 KB
  "src/log.ts": 8 * 1024, // 2 KB
  "src/search.ts": 8 * 1024, // 2 KB
};

async function bundleSize(entry: string, format: "iife" | "esm"): Promise<{ bytes: number; text: string }> {
  const built = await build({
    absWorkingDir: ROOT,
    entryPoints: [entry],
    bundle: true,
    minify: true,
    format,
    target: "chrome116",
    write: false,
    logLevel: "silent",
    // 字体走 dataurl 而不是 file：量体积不写盘，file loader 需要 outdir
    loader: { ".woff": "dataurl", ".woff2": "dataurl", ".ttf": "dataurl" },
  });
  const text = built.outputFiles.map((f) => f.text).join("");
  return { bytes: Buffer.byteLength(text), text };
}

test("内容脚本小到每张网页都付得起", async () => {
  const { bytes } = await bundleSize("src/content.ts", "iife");
  assert.ok(
    bytes <= CONTENT_BUDGET_BYTES,
    `content.ts 打出来 ${Math.round(bytes / 1024)} KB，超过预算 ${CONTENT_BUDGET_BYTES / 1024} KB`,
  );
});

test("按需加载的包各自不超预算", async () => {
  for (const [entry, budget] of Object.entries(LAZY_BUDGET_BYTES)) {
    const { bytes } = await bundleSize(entry, "esm");
    assert.ok(
      bytes <= budget,
      `${entry} 打出来 ${Math.round(bytes / 1024)} KB，超过预算 ${budget / 1024} KB`,
    );
  }
});

test("每个按需加载的入口都在构建配置里登记过", () => {
  for (const entry of Object.keys(LAZY_BUDGET_BYTES)) {
    assert.ok(BUILD_CONFIG.includes(`"${entry}"`), `${entry} 不在 build.mjs 的 entryPoints 里`);
  }
  // content.ts 必须是 IIFE 入口：内容脚本按传统脚本加载，打成 ESM 浏览器不认
  assert.match(BUILD_CONFIG, /iifeEntryPoints:\s*\["src\/content\.ts"\]/);
});
