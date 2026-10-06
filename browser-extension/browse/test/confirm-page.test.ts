/**
 * 确认弹窗页。confirm-page.ts 没有导出：import 即读 URL 填 DOM、绑按钮。
 * jsdom realm + 带参 URL，量的是：三个占位符怎么填、yes/no 各自回什么。
 * 一个文件只 import 一次（同 URL 再 import 是缓存命中，模块不会重跑）；
 * 缺省参数的分支在 confirm-page-boot.test.ts。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

type Any = Record<string, unknown>;
const g = globalThis as Any;

let sent: Any[] = [];
let dom: JSDOM;

afterEach(() => {
  delete g.document;
  delete g.location;
  delete g.chrome;
});

test("fills the three placeholders and answers yes / no with the token", async () => {
  dom = new JSDOM(
    "<!doctype html><body>" +
      '<span id="action"></span><span id="method"></span><span id="url"></span>' +
      '<button id="yes"></button><button id="no"></button>' +
      "</body></html>",
    { url: "https://ext.test/confirm.html?action=删除&method=lg:tab.close&url=https://a.test/p&token=t1" },
  );
  sent = [];
  g.document = dom.window.document;
  g.location = dom.window.location;
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
  };

  await import("../src/confirm-page.ts");
  const $ = (id: string) => dom.window.document.getElementById(id)!;
  assert.equal($("action").textContent, "删除");
  assert.equal($("method").textContent, "lg:tab.close");
  assert.equal($("url").textContent, "https://a.test/p");

  $("yes").click();
  assert.deepEqual(sent[0], { type: "browse-confirm", token: "t1", approved: true });

  $("no").click();
  assert.deepEqual(sent[1], { type: "browse-confirm", token: "t1", approved: false });
});
