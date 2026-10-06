/**
 * 设置页自举失败的另一形态：storage 抛的不是 Error。独立文件 = 独立实例
 * （规矩见 settings.test.ts 顶注）。
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

test("boot 失败抛非 Error 也给出可读的话", async () => {
  const dom = new JSDOM(`<!doctype html><body>${HTML}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  g.chrome = {
    i18n: { getUILanguage: () => "zh-CN", getMessage: (key: string) => `[${key}]` },
    storage: {
      local: {
        get: async () => {
          throw "plain";
        },
        set: async () => {},
        remove: async () => {},
      },
    },
  };

  await import("../src/settings.ts");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(document.getElementById("status")?.textContent, "plain");
});
