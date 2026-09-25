import assert from "node:assert/strict";
import test from "node:test";

import { colorize } from "../src/highlight.ts";

/**
 * 高亮包的薄包装。要钉住的就一条：冷门语言必须返回 null 让调用方保留纯文本，
 * 而不是让底层的 `Unknown language` 抛出去把整页代码变成空白。
 */

test("合集里的语言返回带标签的 HTML", () => {
  const out = colorize("package main", "go");
  assert.ok(out !== null);
  assert.match(out, /<span class="hljs-keyword">package<\/span>/);
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
