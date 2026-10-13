import assert from "node:assert/strict";
import test from "node:test";
import { pageSnapshot, type SnapshotEntry } from "../src/handlers/page.ts";
import { clearChrome, installChrome, ownSession, page, resetRefs, scriptingMock } from "./mock.ts";

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
  // A fresh jsdom page is a fresh ISOLATED world; without this the previous
  // page's refs leak in and numbering continues across pages.
  resetRefs();
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
    ...ownSession(),
    tabs: { query: async () => [{ id: 1, url: "https://example.test/page", active: true, groupId: 500 }] },
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

test("an explicit role attribute wins over the tag name", async () => {
  const { elements } = await snapshot(`<div role="button" id="rb">press</div>`);
  assert.deepEqual(elements.map((e) => e.role), ["button"]);
});

test("zero-sized boxes are invisible even when not display:none", async () => {
  resetRefs();
  const dom = page(`<a href="#z" id="zero" data-zero>z</a><button id="b">ok</button>`);
  // data-zero 元素给 0×0 的盒子：可见性过滤必须把它丢掉
  (dom.window as unknown as { Element: { prototype: Element } }).Element.prototype
    .getBoundingClientRect = function (this: Element) {
    const zero = (this as HTMLElement).hasAttribute?.("data-zero") ?? false;
    const hidden = (this as HTMLElement).style?.display === "none";
    return { width: zero || hidden ? 0 : 100, height: zero || hidden ? 0 : 20 } as DOMRect;
  };
  (globalThis as Record<string, unknown>).getComputedStyle = (el: Element) =>
    (dom.window as unknown as { getComputedStyle: (e: Element) => CSSStyleDeclaration })
      .getComputedStyle(el);
  installChrome({
    ...ownSession(),
    tabs: { query: async () => [{ id: 1, url: "https://example.test/page", active: true, groupId: 500 }] },
    scripting: scriptingMock(),
  });
  try {
    const { elements } = await pageSnapshot({});
    assert.deepEqual(elements.map((e) => e.tag), ["button"]);
  } finally {
    clearChrome();
  }
});

test("refs stay stable for surviving elements across snapshots of one page", async () => {
  resetRefs();
  const dom = page(HTML);
  (dom.window as unknown as { Element: { prototype: Element } }).Element.prototype
    .getBoundingClientRect = function (this: Element) {
    const hidden = (this as HTMLElement).style?.display === "none";
    return { width: hidden ? 0 : 100, height: hidden ? 0 : 20 } as DOMRect;
  };
  (globalThis as Record<string, unknown>).getComputedStyle = (el: Element) =>
    (dom.window as unknown as { getComputedStyle: (e: Element) => CSSStyleDeclaration })
      .getComputedStyle(el);
  installChrome({
    ...ownSession(),
    tabs: { query: async () => [{ id: 1, url: "https://example.test/page", active: true, groupId: 500 }] },
    scripting: scriptingMock(),
  });
  try {
    const first = await pageSnapshot({}) as { elements: SnapshotEntry[] };
    const before = first.elements.map((e) => [e.text, e.ref] as const);
    // Same page, DOM edits only: one element gone, one added, one re-labeled.
    const doc = dom.window.document;
    const buttons = doc.querySelectorAll("button");
    buttons[buttons.length - 1]?.remove(); // Cancel
    const extra = doc.createElement("button");
    extra.textContent = "Later";
    doc.body.appendChild(extra);
    (doc.getElementById("go") as HTMLElement).innerText = "Sign in";
    const second = await pageSnapshot({}) as { elements: SnapshotEntry[] };
    const after = new Map(second.elements.map((e) => [e.text, e.ref] as const));
    // Survivors keep their refs; the newcomer gets a fresh one.
    assert.equal(after.get("Save"), before.find(([t]) => t === "Save")?.[1]);
    assert.equal(after.get("Sign in"), before.find(([t]) => t === "Log in")?.[1]);
    assert.equal(after.has("Cancel"), false);
    assert.ok(!before.some(([, r]) => r === after.get("Later")));
  } finally {
    clearChrome();
  }
});

test("delta reports only what changed since the previous snapshot", async () => {
  resetRefs();
  const dom = page(HTML);
  (dom.window as unknown as { Element: { prototype: Element } }).Element.prototype
    .getBoundingClientRect = function (this: Element) {
    const hidden = (this as HTMLElement).style?.display === "none";
    return { width: hidden ? 0 : 100, height: hidden ? 0 : 20 } as DOMRect;
  };
  (globalThis as Record<string, unknown>).getComputedStyle = (el: Element) =>
    (dom.window as unknown as { getComputedStyle: (e: Element) => CSSStyleDeclaration })
      .getComputedStyle(el);
  installChrome({
    ...ownSession(),
    tabs: { query: async () => [{ id: 1, url: "https://example.test/page", active: true, groupId: 500 }] },
    scripting: scriptingMock(),
  });
  try {
    // First delta call has no baseline: everything counts as added.
    const first = await pageSnapshot({ delta: true }) as {
      delta: { added: SnapshotEntry[]; removed: string[]; changed: SnapshotEntry[] };
    };
    assert.equal(first.delta.added.length, 4);
    assert.deepEqual(first.delta.removed, []);
    assert.deepEqual(first.delta.changed, []);

    const doc = dom.window.document;
    const buttons = doc.querySelectorAll("button");
    buttons[buttons.length - 1]?.remove(); // Cancel
    const extra = doc.createElement("button");
    extra.textContent = "Later";
    doc.body.appendChild(extra);
    (doc.getElementById("go") as HTMLElement).innerText = "Sign in";

    const second = await pageSnapshot({ delta: true }) as {
      delta: { added: SnapshotEntry[]; removed: string[]; changed: SnapshotEntry[] };
    };
    assert.deepEqual(
      second.delta.added.map((e) => e.text),
      ["Later"],
    );
    // "Log in" → "Sign in" is a change, not add+remove: the ref survived.
    assert.equal(second.delta.changed.length, 1);
    assert.equal(second.delta.changed[0]?.text, "Sign in");
    // And the removed ref is exactly Cancel's, taken from the baseline above.
    const gone = first.delta.added.find((e) => e.text === "Cancel")?.ref;
    assert.deepEqual(second.delta.removed, [gone]);
  } finally {
    clearChrome();
  }
});
