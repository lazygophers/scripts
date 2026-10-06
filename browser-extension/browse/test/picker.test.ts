/**
 * desktopCapture 选源小窗。picker.ts 没有导出：import 即按 URL 里的 sources
 * 建按钮。量的是：每个 source 一个按钮、未知 source 保留原名、点击后的回传
 * （streamId 有值 / 没选回 null）。缺省 sources 与缺挂载点的分支在
 * picker-boot.test.ts。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

type Any = Record<string, unknown>;
const g = globalThis as Any;

let sent: Any[] = [];
let closed = 0;
let asked: string[][] = [];
let picked: ((streamId: string | undefined) => void) | null = null;

afterEach(() => {
  delete g.document;
  delete g.location;
  delete g.window;
  delete g.chrome;
});

test("a button per source; clicking answers the stream id, cancelling answers null", async () => {
  const dom = new JSDOM('<!doctype html><body><div id="sources"></div></body>', {
    url: "https://ext.test/picker.html?sources=screen,bogus",
  });
  sent = [];
  closed = 0;
  asked = [];
  picked = null;
  g.document = dom.window.document;
  g.location = dom.window.location;
  g.window = { close: () => (closed += 1) };
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
      chooseDesktopMedia: (sources: string[], cb: (streamId: string | undefined) => void) => {
        asked.push(sources);
        picked = cb;
        return asked.length;
      },
    },
  };

  await import("../src/picker.ts");
  const buttons = dom.window.document.querySelectorAll("button");
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0]!.textContent, "[pickerScreen]");
  assert.equal(buttons[1]!.textContent, "bogus", "未知 source 保留原名");

  buttons[0]!.click();
  assert.deepEqual(asked, [["screen"]]);
  (picked as (streamId: string | undefined) => void)("sid-9");
  assert.deepEqual(sent[0], { type: "browse-pick", streamId: "sid-9" });
  assert.equal(closed, 1, "选完关窗");

  buttons[0]!.click();
  (picked as (streamId: string | undefined) => void)(undefined);
  assert.deepEqual(sent[1], { type: "browse-pick", streamId: null }, "没选就关窗回 null");
  assert.equal(closed, 2);
});
