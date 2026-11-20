import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { colorize } from "../src/highlight.ts";
import { contrast, PALETTES, TOKEN_NAMES } from "../src/themes.ts";

/**
 * 高亮包的薄包装。要钉住的就一条：冷门语言必须返回 null 让调用方保留纯文本，
 * 而不是让底层的 `Unknown language` 抛出去把整页代码变成空白。
 */

test("合集里的语言返回带标签的 HTML", () => {
  const out = colorize("package main", "go");
  assert.ok(out !== null);
  assert.match(out, /<span class="hljs-keyword">package<\/span>/);
});

test("diff 新增、删除和字符差异在灰阶下仍可区分", () => {
  const out = colorize("@@ -1 +1 @@\n-old\n+new\n", "diff") ?? "";
  assert.match(out, /class="hljs-deletion"/);
  assert.match(out, /class="hljs-addition"/);

  const css = readFileSync(new URL("../src/viewer.css", import.meta.url), "utf8");
  assert.match(css, /\.hljs-addition\s*\{[^}]*color:\s*var\(--foreground\)[^}]*background-color:[^}]*border-left:\s*3px solid var\(--syn-string\)[^}]*text-decoration:\s*underline solid/s);
  assert.match(css, /\.hljs-deletion\s*\{[^}]*color:\s*var\(--foreground\)[^}]*background-color:\s*color-mix\(in oklab, var\(--destructive\)[^}]*border-left:\s*3px dashed var\(--destructive\)[^}]*text-decoration:\s*line-through dashed/s);
  assert.match(css, /\.hljs-addition-emphasis\s*\{[^}]*color:\s*var\(--foreground\)[^}]*text-decoration:\s*underline/s);
  assert.match(css, /\.hljs-deletion-emphasis\s*\{[^}]*color:\s*var\(--foreground\)[^}]*text-decoration:\s*line-through/s);

  assert.ok(TOKEN_NAMES.includes("syn-string"));
  assert.ok(TOKEN_NAMES.includes("destructive"));
  assert.match(css, /data-lfv-palette="eink"\] \.hljs-addition,\s+html\.lfv-on\[data-lfv-palette="eink"\] \.hljs-addition-emphasis\s*\{[^}]*background-color:\s*#e1e1e1/s);
  assert.match(css, /data-lfv-palette="eink"\] \.hljs-deletion\s*\{[^}]*background-color:\s*#c1c1c1/);
  assert.match(css, /data-lfv-palette="einkd"\] \.hljs-addition\s*\{[^}]*background-color:\s*#454545/);
  assert.match(css, /data-lfv-palette="einkd"\] \.hljs-deletion\s*\{[^}]*background-color:\s*#292929/);
  for (const background of ["#e1e1e1", "#c1c1c1"]) assert.ok(contrast("#414141", background) >= 4.5);
  for (const background of ["#454545", "#292929"]) assert.ok(contrast("#e0e0e0", background) >= 4.5);
});

test("公式主题沿用协调 token 阶梯，上游主题通过行内原色覆盖", () => {
  const css = readFileSync(new URL("../src/viewer.css", import.meta.url), "utf8");
  const light = /html\.lfv-on\[data-lfv-polarity="light"\]\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
  const dark = /html\.lfv-on\[data-lfv-polarity="dark"\]\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
  assert.ok(light);
  assert.ok(dark);

  for (const token of TOKEN_NAMES) {
    assert.match(light, new RegExp(`--${token}\\s*:`), `light theme defines ${token}`);
    assert.match(dark, new RegExp(`--${token}\\s*:`), `dark theme defines ${token}`);
  }
  assert.match(light, /--background:\s*oklch\(98%/);
  assert.match(light, /--foreground:\s*oklch\(24%/);
  assert.match(dark, /--background:\s*oklch\(19%/);
  assert.match(dark, /--foreground:\s*oklch\(93%/);

  for (const [token, offset] of [["accent", 180], ["syn-keyword", 60], ["syn-string", 120], ["syn-number", 240], ["syn-function", 180], ["syn-type", 300]] as const) {
    assert.match(light, new RegExp(`--${token}:\\s*oklch\\([^;]*calc\\(var\\(--lfv-h\\) \\+ ${offset}\\)`));
    assert.match(dark, new RegExp(`--${token}:\\s*oklch\\([^;]*calc\\(var\\(--lfv-h\\) \\+ ${offset}\\)`));
  }
  assert.equal(PALETTES.filter((palette) => palette.tokens === undefined && palette.polarity === "light").length, 4);
  assert.equal(PALETTES.filter((palette) => palette.tokens === undefined && palette.polarity === "dark").length, 4);
  assert.equal(PALETTES.filter((palette) => palette.tokens !== undefined).length, 11);
});

test("不在合集里的语言返回 null，不抛 Unknown language", () => {
  assert.equal(colorize("main :: IO ()", "brainfuck"), null);
  assert.equal(colorize("x", ""), null);
});

test("别名也认（js 就是 javascript）", () => {
  assert.ok(colorize("const x = 1;", "js") !== null);
});

test("HTML 字符在输出里是转义过的，不会变成真标签", () => {
  const out = colorize("const html = '<b>x</b>';", "javascript") ?? "";
  assert.ok(!out.includes("<b>"), "源码里的 <b> 必须被转义");
  assert.match(out, /&lt;b&gt;/);
});

test("语法写错也照样着色，不抛异常（ignoreIllegals）", () => {
  assert.doesNotThrow(() => colorize("func ( { ] )))", "go"));
  assert.ok(colorize("func ( { ] )))", "go") !== null);
});

test("空代码返回空串而不是 null", () => {
  assert.equal(colorize("", "go"), "");
});
