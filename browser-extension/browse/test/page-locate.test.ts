/**
 * 注入脚本：page-locate.ts 只做一件事 —— 把 locateInPage 挂到页面全局上，
 * 好让 `chrome.scripting.executeScript({ files: [...] })` 把嵌套的辅助函数
 * 完整带进页面（func: 序列化会丢闭包）。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";

type Any = Record<string, unknown>;
const g = globalThis as Any;

test("importing the injectable file exposes locateInPage on the page global", async () => {
  const { locateInPage } = await import("../src/locator.ts");
  await import("../src/page-locate.ts");
  assert.equal((g.__browseLocate as unknown) === locateInPage, true);
  assert.equal(typeof g.__browseLocate, "function");
});

afterEach(() => {
  delete g.__browseLocate;
});
