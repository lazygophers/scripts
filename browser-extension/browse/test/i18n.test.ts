/**
 * 语言文件的完整性。这里不测 `chrome.i18n` 本身（那是浏览器实现的），只测我们能写
 * 错的两件事：两个语言的 key 不一样，和页面上引用了一个不存在的 key —— 两者都让用户
 * 在某个语言下看到空白或英文 key。
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const SRC = join(import.meta.dirname, "..", "src");
const LOCALES = join(SRC, "_locales");

function messages(lang: string): Record<string, { message: string }> {
  return JSON.parse(readFileSync(join(LOCALES, lang, "messages.json"), "utf8"));
}

const langs = readdirSync(LOCALES).sort();

test("only zh_CN ships — the extension is Chinese-only by design", () => {
  // 2026-09-15：删掉 en。Chrome 按浏览器界面语言挑语言包，英文界面的浏览器会拿到英文
  // UI；这个扩展（CLI 也是全中文）只有中文一种目标受众，en 只会让用户看到非中文界面。
  assert.deepEqual(langs, ["zh_CN"]);
});

test("every locale has exactly the same keys", () => {
  const base = Object.keys(messages("zh_CN")).sort();
  for (const lang of langs) {
    assert.deepEqual(Object.keys(messages(lang)).sort(), base, `${lang} 的 key 集合不一致`);
  }
});

test("no message is empty", () => {
  for (const lang of langs) {
    for (const [key, entry] of Object.entries(messages(lang))) {
      assert.ok(entry.message.trim().length > 0, `${lang}/${key} 是空的`);
    }
  }
});

test("every key referenced by the pages exists", () => {
  const known = new Set(Object.keys(messages("zh_CN")));
  const used = new Set<string>();
  const files = [
    "panel.html",
    "confirm.html",
    "settings.html",
    "panel.ts",
    "confirm-page.ts",
    "settings.ts",
    "manifest.json",
  ];
  for (const file of files) {
    const text = readFileSync(join(SRC, file), "utf8");
    for (const [, key] of text.matchAll(/data-i18n(?:-placeholder)?="([^"]+)"/g)) used.add(key);
    for (const [, key] of text.matchAll(/\bmsg\(\s*"([^"]+)"/g)) used.add(key);
    for (const [, key] of text.matchAll(/__MSG_([A-Za-z0-9_@]+)__/g)) used.add(key);
  }
  // 分支里才用得到的两个 key 不是字面量参数，单独补进来
  used.add("panelDaemonConnected");
  used.add("panelDaemonDisconnected");
  assert.ok(used.size > 10, `只扫到 ${used.size} 个 key，正则大概失效了`);
  for (const key of used) {
    assert.ok(known.has(key), `页面引用了不存在的 key: ${key}`);
  }
});

/**
 * manifest 和 package.json 的版本号必须一致。
 *
 * 两处分居两地，改一个忘另一个是必然会发生的事，而且不会有任何报错 —— 打出来的包
 * 版本号对不上，排查时先怀疑的一定是别的地方。和 gecko.id 那条同样的思路：把「两处
 * 要同步」变成机器强制。
 */
test("the manifest and package.json agree on the version", () => {
  const root = join(import.meta.dirname, "..");
  const manifest = JSON.parse(readFileSync(join(root, "src/manifest.json"), "utf8"));
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  assert.equal(manifest.version, pkg.version);
});
