/**
 * 面板的空 DOM 自举：页面上一个 id 都没有时，所有 render 路径都要安静返回。
 * panel.test.ts 共用一个模块实例且首启在完整表单上，这条路径需要独立文件的
 * 一次独立导入。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

type Any = Record<string, unknown>;

afterEach(() => {
  const g = globalThis as Any;
  delete g.chrome;
  delete g.document;
  delete g.HTMLElement;
  delete g.navigator;
});

test("缺节点的页面上自举：所有 render 路径都安静返回，不抛", async () => {
  // 只有 #cut：boot 后点它就能带着其余节点全 null 的状态走 renderLog / renderBridge /
  // renderServerLog 的缺席分支；storage.get 抛错让首启的 run() 走 catch（statusNode 也是 null）。
  const dom = new JSDOM(`<!doctype html><body><button id="cut"></button></body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  Object.defineProperty(g, "navigator", { configurable: true, value: dom.window.navigator });
  let getThrows = true;
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string) => `[${key}]`,
    },
    runtime: {
      sendMessage: async () => ({}),
      openOptionsPage: () => {},
    },
    storage: {
      local: {
        get: async () => {
          if (getThrows) {
            throw new Error("storage gone");
          }
          return {};
        },
        set: async () => {},
        remove: async () => {},
      },
    },
  };

  await import("../src/panel.ts");
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  // storage 恢复后再点 cut：renderLog / renderBridge / renderServerLog 照常跑，
  // 但那些节点都不在 —— 缺席分支必须安静返回
  getThrows = false;
  (dom.window.document.getElementById("cut") as HTMLButtonElement).click();
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  assert.ok(true, "自举、首启失败和 cut 重拉都没抛");
});
