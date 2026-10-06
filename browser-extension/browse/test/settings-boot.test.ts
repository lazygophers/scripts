/**
 * 设置页的另一种自举：插件自己的存储坏了。settings.test.ts 共用一个模块实例，
 * 自举只跑一次；这条失败路径需要独立文件里的一次独立导入（无 query —— 规矩见
 * settings.test.ts 顶注）。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

const SRC = join(import.meta.dirname, "..", "src");
const HTML = readFileSync(join(SRC, "settings.html"), "utf8");

type Any = Record<string, unknown>;

afterEach(() => {
  const g = globalThis as Any;
  delete g.document;
  delete g.HTMLElement;
  delete g.chrome;
});

test("插件自己的存储坏了才显示离线，并且不画一份空配置上去", async () => {
  const dom = new JSDOM(`<!doctype html><body>${HTML}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string) => `[${key}]`,
    },
    storage: {
      local: {
        get: async () => {
          throw new Error("storage is gone");
        },
        set: async () => {},
        remove: async () => {},
      },
    },
  };

  await import("../src/settings.ts");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(document.body.classList.contains("offline"), true, "表单必须被禁掉");
  assert.equal(document.getElementById("status")?.className, "bad");
  assert.equal(document.getElementById("path")?.textContent, "…", "不能让用户以为设置丢了");
});
