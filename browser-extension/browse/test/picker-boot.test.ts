/**
 * 选源小窗的空 DOM 自举：没有 #sources 挂载点，sources 参数也不给。
 * picker.test.ts 共用一份带挂载点的页面，缺席分支需要独立文件的一次独立导入
 * （同进程里同路径的重复 import 只有最后一个实例进覆盖率）。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

type Any = Record<string, unknown>;
const g = globalThis as Any;

afterEach(() => {
  delete g.document;
  delete g.location;
  delete g.window;
  delete g.chrome;
});

test("缺挂载点的页面上自举：不炸、不回话", async () => {
  const dom = new JSDOM("<!doctype html><body><p>nothing</p></body>", {
    url: "https://ext.test/picker.html",
  });
  const sent: Any[] = [];
  g.document = dom.window.document;
  g.location = dom.window.location;
  g.window = { close: () => {} };
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string) => `[${key}]`,
    },
    runtime: {
      sendMessage: async (message: Any) => {
        sent.push(message);
      },
    },
    desktopCapture: {
      chooseDesktopMedia: () => 1,
    },
  };
  await import("../src/picker.ts");
  assert.deepEqual(sent, []);
});
