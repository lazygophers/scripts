import assert from "node:assert/strict";
import test from "node:test";
import { pageSnapshot, type SnapshotEntry } from "../src/handlers/page.ts";
import { clearChrome, installChrome, page, scriptingMock } from "./mock.ts";

const HTML = `
  <a href="/login" id="go">Log in</a>
  <button>Save</button>
  <button>Cancel</button>
  <input name="user" placeholder="user name" />
  <input type="hidden" name="csrf" value="x" />
  <span>not interactive</span>
`;

async function snapshot(html: string, params: Record<string, unknown> = {}): Promise<{
  elements: SnapshotEntry[];
  truncated: boolean;
}> {
  const dom = page(html);
  // jsdom lays nothing out, so every rect is 0×0; the visibility filter would
  // drop the whole page. Report a real box for anything that is not display:none.
  (dom.window as unknown as { Element: { prototype: Element } }).Element.prototype
    .getBoundingClientRect = function (this: Element) {
    const hidden = (this as HTMLElement).style?.display === "none";
    return { width: hidden ? 0 : 100, height: hidden ? 0 : 20 } as DOMRect;
  };
  (globalThis as Record<string, unknown>).getComputedStyle = (el: Element) =>
    (dom.window as unknown as { getComputedStyle: (e: Element) => CSSStyleDeclaration })
      .getComputedStyle(el);
  installChrome({
    tabs: { query: async () => [{ id: 1, url: "https://example.test/page", active: true }] },
    scripting: scriptingMock(),
  });
  try {
    return await pageSnapshot(params);
  } finally {
    clearChrome();
  }
}

test("snapshot lists the interactive elements, skipping the rest", async () => {
  const { elements } = await snapshot(HTML);
  assert.deepEqual(
    elements.map((e) => e.text),
    ["Log in", "Save", "Cancel", "user name"],
  );
  // A hidden input is not something anyone can click or type into.
  assert.ok(!elements.some((e) => e.role === "input[hidden]"));
});

test("every entry carries a css and an xpath that really resolve to it", async () => {
  const dom = page(HTML);
  const { elements } = await snapshot(HTML);
  const doc = dom.window.document;
  for (const entry of elements) {
    assert.equal(doc.querySelectorAll(entry.css).length, 1, `css ${entry.css}`);
    const hit = doc.evaluate(
      entry.xpath,
      doc,
      null,
      dom.window.XPathResult.FIRST_ORDERED_NODE_TYPE,
      null,
    ).singleNodeValue;
    assert.ok(hit, `xpath ${entry.xpath} found nothing`);
  }
  // Two identical <button>s must not share one locator.
  const buttons = elements.filter((e) => e.tag === "button");
  assert.notEqual(buttons[0]?.css, buttons[1]?.css);
});

test("an id ends the css path, and limit truncates instead of dumping a feed", async () => {
  const { elements, truncated } = await snapshot(HTML, { limit: 2 });
  assert.equal(elements.length, 2);
  assert.equal(truncated, true);
  assert.equal(elements[0]?.css, "#go");
});
