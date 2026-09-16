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
