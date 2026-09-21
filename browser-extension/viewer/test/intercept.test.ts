import assert from "node:assert/strict";
import test from "node:test";
import { FORCED_EXTS, fileOf, rules } from "../src/intercept.ts";

/**
 * 拦截协议（CONTEXT.md）：规则生成（发端）和 file 参数解码（收端）的两端一致性。
 * 两半在 2026-09-21 之前散在 rules.ts / page.ts，靠默契对齐 —— 现在同住 intercept.ts。
 */

test("协议两端一致：规则造出的地址，fileOf 能取回原文件", () => {
  const base = "chrome-extension://abc/";
  for (const ext of FORCED_EXTS) {
    const file = `file:///tmp/config.${ext}`;
    // dNR 的 \0 = 整段匹配：照规则真正会发生的替换拼出地址
    const [rule] = rules(base).filter((r) =>
      new RegExp(String(r.condition.regexFilter)).test(file));
    const sub = rule?.action.redirect?.regexSubstitution ?? "";
    const redirect = sub.replace("\\0", file);
    assert.match(redirect, /^chrome-extension:\/\/abc\/viewer\.html\?file=file:/);
    assert.equal(fileOf(redirect), file, `${ext} 的地址要原样取回`);
  }
});

test("file 参数带 % 编码按标准解码（searchParams 语义）", () => {
  assert.equal(
    fileOf(`chrome-extension://viewer/viewer.html?file=${encodeURIComponent("file:///tmp/a b.csv")}`),
    "file:///tmp/a b.csv",
  );
});

test("缺 file 参数返回空串，不抛", () => {
  assert.equal(fileOf("chrome-extension://viewer/viewer.html"), "");
});

test("规则只拦整页导航，条数与 FORCED_EXTS 对齐，id 稳定", () => {
  const list = rules("chrome-extension://abc/");
  assert.equal(list.length, FORCED_EXTS.length);
  list.forEach((rule, index) => {
    assert.equal(rule.id, index + 1);
    assert.deepEqual(rule.condition.resourceTypes, ["main_frame"]);
    assert.match(String(rule.action.redirect?.regexSubstitution), /viewer\.html\?file=\\0$/);
  });
});

test("已知边界：文件路径里的字面 & 会截断 query（dNR 不能编码，钉死现状）", () => {
  // 换成静态规则集或 webRequest 也解不了 —— 接受边界，测试保证它是有意为之
  assert.equal(
    fileOf("chrome-extension://viewer/viewer.html?file=file:///tmp/a&b.yaml"),
    "file:///tmp/a",
  );
});
