import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_CUSTOM,
  DEFAULT_PALETTE,
  DEFAULT_STYLE,
  FIELDS,
  PALETTES,
  STYLES,
  TOKEN_NAMES,
  applyTheme,
  hexOf,
} from "../src/themes.ts";
import { page } from "./mock.ts";

/**
 * 主题写到 `<html>` 上的那几个行内变量和 data 属性。颜色本身由 viewer.css 的
 * `oklch()` 公式推，这里只负责给 `--lfv-h` / `--lfv-c` 和挑对配色，所以挑错了
 * 整页颜色就全错——而这段之前没有测试。
 */

test("默认配色和默认风格都真的存在于表里", () => {
  assert.ok(PALETTES.some((p) => p.id === DEFAULT_PALETTE));
  assert.ok(STYLES.some((s) => s.id === DEFAULT_STYLE));
});

test("配色表里没有重复 id，亮面四套在前暗面四套在后", () => {
  const ids = PALETTES.map((p) => p.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual(PALETTES.map((p) => p.polarity), [
    "light", "light", "light", "light", "dark", "dark", "dark", "dark",
  ]);
});

test("设置页可调项都是真实的 token 名", () => {
  for (const field of FIELDS) assert.ok(TOKEN_NAMES.includes(field.key), field.key);
});

test("选中的配色写成色相、彩度和 data 属性", () => {
  const dom = page("");
  const applied = applyTheme(dom.window.document, "ochre", "press");
  const root = dom.window.document.documentElement;

  assert.equal(applied.palette.id, "ochre");
  assert.equal(applied.style.id, "press");
  assert.equal(applied.custom, false);
  assert.equal(root.style.getPropertyValue("--lfv-h"), "40");
  assert.equal(root.style.getPropertyValue("--lfv-c"), "1.15");
  assert.equal(root.dataset["lfvPalette"], "ochre");
  assert.equal(root.dataset["lfvPolarity"], "light");
  assert.equal(root.dataset["lfvStyle"], "press");
  assert.equal(root.style.colorScheme, "light");
});

test("纯灰阶那两套彩度为 0", () => {
  const dom = page("");
  applyTheme(dom.window.document, "eink");
  assert.equal(dom.window.document.documentElement.style.getPropertyValue("--lfv-c"), "0");
});

test("认不出的配色或风格退回默认，不把页面弄成没颜色", () => {
  const dom = page("");
  const applied = applyTheme(dom.window.document, "不存在的配色", "不存在的风格");
  assert.equal(applied.palette.id, DEFAULT_PALETTE);
  assert.equal(applied.style.id, DEFAULT_STYLE);
});

test("自定义配色以 base 为底，只有改过的 token 被盖住", () => {
  const dom = page("");
  const applied = applyTheme(dom.window.document, "custom", "console", {
    base: "soot",
    patch: { accent: "#ff0000" },
  });
  const root = dom.window.document.documentElement;

  assert.equal(applied.custom, true);
  assert.equal(applied.palette.id, "soot", "底子仍是 base 那一套");
  assert.equal(root.dataset["lfvPalette"], "soot");
  assert.equal(root.style.getPropertyValue("--accent"), "#ff0000");
  assert.equal(root.style.getPropertyValue("--foreground"), "", "没改过的仍由公式算");
});

test("从自定义切回现成配色时，之前盖上去的值被清掉", () => {
  const dom = page("");
  const doc = dom.window.document;
  applyTheme(doc, "custom", DEFAULT_STYLE, { base: "pool", patch: { accent: "#ff0000" } });
  assert.equal(doc.documentElement.style.getPropertyValue("--accent"), "#ff0000");

  applyTheme(doc, "brass");
  assert.equal(doc.documentElement.style.getPropertyValue("--accent"), "");
});

test("默认的自定义配置不盖任何 token", () => {
  const dom = page("");
  applyTheme(dom.window.document, "custom", DEFAULT_STYLE, DEFAULT_CUSTOM);
  for (const name of TOKEN_NAMES) {
    assert.equal(dom.window.document.documentElement.style.getPropertyValue(`--${name}`), "", name);
  }
});

test("hexOf 直接认 #rrggbb，统一成小写", () => {
  const dom = page("");
  assert.equal(hexOf(dom.window.document, "#AABBCC"), "#aabbcc");
});

test("hexOf 把老式 rgb() 换算成色号", () => {
  const dom = page("");
  assert.equal(hexOf(dom.window.document, "rgb(1, 2, 3)"), "#010203");
});

test("hexOf 取不到画布时原样返回，不假装算得出", () => {
  const dom = page("");
  // jsdom 没实现 canvas；`oklch()` 这类新写法只能原样回去，绝不能抠出一串小数当 RGB
  const value = "oklch(0.5 0.1 200)";
  assert.equal(hexOf(dom.window.document, value), value);
});

test("hexOf 用完就把探针节点摘掉", () => {
  const dom = page("");
  const before = dom.window.document.body.childElementCount;
  hexOf(dom.window.document, "#ffffff");
  assert.equal(dom.window.document.body.childElementCount, before);
});
