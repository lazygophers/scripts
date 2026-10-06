import { deepStrictEqual } from "node:assert/strict";
import { test } from "node:test";

import { checkPackage } from "../package.mjs";

const manifest = {
  manifest_version: 3,
  name: "viewer",
  version: "0.0.1",
  content_scripts: [{ js: ["content.js"] }],
  background: { service_worker: "background.js", type: "module" },
  options_page: "settings.html",
  web_accessible_resources: [{ resources: ["viewer.css", "*.woff2"] }],
};

const files = [
  "manifest.json",
  "content.js",
  "background.js",
  "settings.html",
  "viewer.css",
  "katex-x.woff2",
];

test("文件齐全的包没有问题", () => {
  deepStrictEqual(checkPackage(files, manifest), []);
});

test("manifest 点到名的文件少一个就不放行", () => {
  deepStrictEqual(
    checkPackage(files.filter((name) => name !== "background.js"), manifest),
    ["manifest 点到名的文件不在包里：background.js"],
  );
});

test("带 * 的条目是给浏览器匹配用的，不当成文件去找", () => {
  // 包里那个 woff2 叫别的名字，`*.woff2` 仍然算过。
  deepStrictEqual(checkPackage(files, manifest), []);
});

test("firefox 版的 background.scripts 一样要检查", () => {
  const firefox = { ...manifest, background: { scripts: ["background.js"] } };
  deepStrictEqual(checkPackage(["manifest.json", "content.js", "settings.html", "viewer.css"], firefox), [
    "manifest 点到名的文件不在包里：background.js",
  ]);
});

test("manifest 本身不合法就不放行", () => {
  deepStrictEqual(checkPackage(["manifest.json"], { manifest_version: 2, name: "x" }), [
    "manifest_version 不是 3",
    "manifest 少了 version",
  ]);
});

test("sourcemap 不许进包", () => {
  deepStrictEqual(checkPackage([...files, "markdown.js.map"], manifest), [
    "打包不该带上 sourcemap：markdown.js.map",
  ]);
});

test("html 里 <script src> 指到一个不在包里的文件也要拦下来", () => {
  const html = '<link rel="stylesheet" href="viewer.css" /><script src="settings.js"></script>';
  deepStrictEqual(checkPackage(files, manifest, { "settings.html": html }), [
    "settings.html 里引的文件不在包里：settings.js",
  ]);
});

test("html 里的网址和页内锚点不当成包里的文件", () => {
  const html = `<a href="https://example.test/x">x</a><a href="#top">顶部</a>
    <link rel="stylesheet" href="viewer.css" /><script src="background.js"></script>`;
  deepStrictEqual(checkPackage(files, manifest, { "settings.html": html }), []);
});

test("html 引到包外的文件、sourcemap、坏 manifest 各自报出来", () => {
  deepStrictEqual(
    checkPackage(files, manifest, { "settings.html": '<script src="missing.js"></script>' }),
    ["settings.html 里引的文件不在包里：missing.js"],
  );
  deepStrictEqual(checkPackage([...files, "x.map"], manifest), [
    "打包不该带上 sourcemap：x.map",
  ]);
  deepStrictEqual(
    checkPackage(files, { manifest_version: 2, name: "viewer", version: "1" }),
    ["manifest_version 不是 3"],
  );
  deepStrictEqual(checkPackage(files, { manifest_version: 3, name: "viewer" }), [
    "manifest 少了 version",
  ]);
});

test("referenced 的其余形状：scripts 数组、popup、css、锚点与外链不算", () => {
  const m = {
    manifest_version: 3,
    name: "viewer",
    version: "1",
    background: { scripts: ["sw.js"] },
    action: { default_popup: "popup.html" },
    content_scripts: [{ js: ["c.js"], css: ["c.css"] }],
  };
  const have = ["manifest.json", "sw.js", "popup.html", "c.js", "c.css"];
  deepStrictEqual(checkPackage(have, m, {
    "popup.html": '<a href="https://x.test/">e</a><a href="#top">t</a><script src="c.js"></script>',
  }), []);
  deepStrictEqual(checkPackage(have.filter((f) => f !== "popup.html"), m), [
    "manifest 点到名的文件不在包里：popup.html",
  ]);
});

test("packDist：真目录验过压成 zip，验不过整条命令失败", async () => {
  const { mkdtemp, writeFile, mkdir } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { packDist } = await import("../package.mjs");

  const root = await mkdtemp(join(tmpdir(), "packdist-"));
  const dist = join(root, "dist");
  await mkdir(dist);
  await writeFile(join(dist, "manifest.json"), JSON.stringify({
    manifest_version: 3, name: "viewer", version: "0.0.1",
  }));
  await writeFile(join(dist, "a.txt"), "x");
  // dist 里有 html 才会走 packDist 的页面读取支；html 引用包内存在的文件
  await mkdir(join(dist, "sub"));
  await writeFile(join(dist, "sub", "page.html"), '<script src="a.txt"></script>');
  await writeFile(join(dist, "sub", "leaf.js"), "x");

  const zip = await packDist({ dist, out: join(root, "out"), name: "p.zip" });
  const { stat } = await import("node:fs/promises");
  assertZip(await stat(zip));

  await writeFile(join(dist, "b.map"), "{}");
  await assert.rejects(
    () => packDist({ dist, out: join(root, "out"), name: "bad.zip" }),
    /sourcemap/,
  );
  await writeFile(join(dist, "sub", "page.html"), '<script src="missing.js"></script>');
  await assert.rejects(
    () => packDist({ dist, out: join(root, "out"), name: "bad2.zip" }),
    /sub\/page.html 里引的文件不在包里/,
  );
  await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
});

function assertZip(stat) {
  if (!stat.isFile() || stat.size === 0) throw new Error("zip 没打出来");
}

test("packDist 的 zip 命令不在时给出可读错误", async () => {
  const { mkdtemp, writeFile, mkdir } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { packDist } = await import("../package.mjs");
  const root = await mkdtemp(join(tmpdir(), "packdist-"));
  const dist = join(root, "dist");
  await mkdir(dist);
  await writeFile(join(dist, "manifest.json"), JSON.stringify({
    manifest_version: 3, name: "viewer", version: "0.0.1",
  }));
  const realPath = process.env.PATH;
  process.env.PATH = "/nonexistent";
  try {
    await assert.rejects(() => packDist({ dist, out: join(root, "out"), name: "z.zip" }), /zip 失败/);
  } finally {
    process.env.PATH = realPath;
  }
  await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
});

import assert from "node:assert/strict";
import { join } from "node:path";

test("packDist：zip 在但退出非 0，报退出码而不是 error.message", async () => {
  const { mkdtemp, writeFile, mkdir } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { packDist } = await import("../package.mjs");
  const root = await mkdtemp(join(tmpdir(), "packdist-"));
  const dist = join(root, "dist");
  await mkdir(dist);
  await writeFile(join(dist, "manifest.json"), JSON.stringify({
    manifest_version: 3, name: "viewer", version: "0.0.1",
  }));
  // PATH 前置一个假 zip：能找到（spawnSync 无 error）但退出码 3
  const bin = join(root, "bin");
  await mkdir(bin);
  await writeFile(join(bin, "zip"), "#!/bin/sh\nexit 3\n");
  await (await import("node:fs/promises")).chmod(join(bin, "zip"), 0o755);
  const realPath = process.env.PATH;
  process.env.PATH = `${bin}:${realPath}`;
  try {
    await assert.rejects(
      () => packDist({ dist, out: join(root, "out"), name: "z.zip" }),
      /退出码 3/,
    );
  } finally {
    process.env.PATH = realPath;
    await (await import("node:fs/promises")).rm(root, { recursive: true, force: true });
  }
});
