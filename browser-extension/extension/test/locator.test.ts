import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";
import { locateInPage, pageErrorCode, parseLocator } from "../src/locator.ts";

/** Install a jsdom document as the globals `locateInPage` reads off the realm. */
function page(html: string): JSDOM {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`);
  const g = globalThis as unknown as Record<string, unknown>;
  g.document = dom.window.document;
  g.Element = dom.window.Element;
  g.XPathResult = dom.window.XPathResult;
  g.MutationObserver = dom.window.MutationObserver;
  return dom;
}

const NO_WAIT = { wait: false } as const;

test("prefix splits on the first = only, so values may contain more", () => {
  assert.deepEqual(parseLocator(`css=a[href="x=1"]`), {
    scheme: "css",
    value: `a[href="x=1"]`,
  });
  assert.deepEqual(parseLocator("text=a=b"), { scheme: "text", value: "a=b" });
  assert.deepEqual(parseLocator("text*=登录"), { scheme: "text*", value: "登录" });
  assert.deepEqual(parseLocator("xpath=//a"), { scheme: "xpath", value: "//a" });
  assert.deepEqual(parseLocator("js=document.body"), {
    scheme: "js",
    value: "document.body",
  });
});

test("no prefix, and an unknown prefix, both mean css", () => {
  assert.deepEqual(parseLocator("button.login"), {
    scheme: "css",
    value: "button.login",
  });
  assert.deepEqual(parseLocator(`a[href="x=1"]`), {
    scheme: "css",
    value: `a[href="x=1"]`,
  });
});

test("css hits, misses, and honours index and all", async () => {
  page(`<b class="x">1</b><b class="x">2</b><b class="x">3</b>`);
  const [first] = await locateInPage("css", "b.x", NO_WAIT);
  assert.equal(first?.textContent, "1");

  const [second] = await locateInPage("css", "b.x", { wait: false, index: 1 });
  assert.equal(second?.textContent, "2");

  const every = await locateInPage("css", "b.x", { wait: false, all: true });
  assert.equal(every.length, 3);

  await assert.rejects(
    locateInPage("css", "b.missing", NO_WAIT),
    /no such element/,
  );
  await assert.rejects(
    locateInPage("css", "b.x", { wait: false, index: 9 }),
    /no such element/,
  );
});

test("a malformed css selector is invalid argument, not no such element", async () => {
  page(`<b>1</b>`);
  await assert.rejects(locateInPage("css", "b[[[", NO_WAIT), /invalid argument/);
});

test("xpath hits and misses", async () => {
  page(`<button type="submit">Go</button><button>No</button>`);
  const [el] = await locateInPage("xpath", `//button[@type="submit"]`, NO_WAIT);
  assert.equal(el?.textContent, "Go");
  await assert.rejects(
    locateInPage("xpath", `//button[@type="reset"]`, NO_WAIT),
    /no such element/,
  );
});

test("text= is exact and whitespace-normalised, text*= is contains", async () => {
  page(`<button>  登录\n  账号 </button><span>请先登录账号再继续</span>`);
  const [exact] = await locateInPage("text", "登录 账号", NO_WAIT);
  assert.equal(exact?.tagName, "BUTTON");

  await assert.rejects(locateInPage("text", "登录", NO_WAIT), /no such element/);

  const partial = await locateInPage("text*", "登录", { wait: false, all: true });
  assert.deepEqual(
    partial.map((el) => el.tagName),
    ["BUTTON", "SPAN"],
  );
});

test("text= returns the deepest match, not its ancestors", async () => {
  page(`<div><p><span>登录</span></p></div>`);
  const [el] = await locateInPage("text", "登录", NO_WAIT);
  assert.equal(el?.tagName, "SPAN");
});

test("text= skips display:none, visibility:hidden, opacity:0 and [hidden]", async () => {
  page(`
    <span style="display:none">登录</span>
    <span style="visibility:hidden">登录</span>
    <span style="opacity:0">登录</span>
    <span hidden>登录</span>
    <span id="real">登录</span>
  `);
  const hits = await locateInPage("text", "登录", { wait: false, all: true });
  assert.equal(hits.length, 1);
  assert.equal((hits[0] as HTMLElement).id, "real");
});

test("text= skips a node hidden by an ancestor", async () => {
  page(`<div style="display:none"><p><span>登录</span></p></div><b>登录</b>`);
  const hits = await locateInPage("text", "登录", { wait: false, all: true });
  assert.equal(hits.length, 1);
  assert.equal(hits[0]?.tagName, "B");
});

test("js= returns the element, and rejects a non-Element result", async () => {
  page(`<div id="host"><b>deep</b></div>`);
  const [el] = await locateInPage("js", `document.querySelector("#host b")`, NO_WAIT);
  assert.equal(el?.textContent, "deep");
  await assert.rejects(locateInPage("js", `1 + 1`, NO_WAIT), /invalid argument/);
  await assert.rejects(locateInPage("js", `nope.nope`, NO_WAIT), /invalid argument/);
});

test("auto-wait resolves when the element shows up late", async () => {
  const dom = page(`<div id="root"></div>`);
  setTimeout(() => {
    const el = dom.window.document.createElement("b");
    el.className = "late";
    el.textContent = "hi";
    dom.window.document.getElementById("root")?.append(el);
  }, 60);
  const [el] = await locateInPage("css", "b.late", { timeout: 2000 });
  assert.equal(el?.textContent, "hi");
});

test("auto-wait resolves when an existing element becomes visible late", async () => {
  const dom = page(`<b class="late" style="display:none">hi</b>`);
  setTimeout(() => {
    const el = dom.window.document.querySelector<HTMLElement>("b.late");
    if (el) el.style.display = "block";
  }, 60);
  const [el] = await locateInPage("css", "b.late", { timeout: 2000 });
  assert.equal(el?.textContent, "hi");
});

test("auto-wait gives up with no such element after the timeout", async () => {
  page(`<div></div>`);
  const started = Date.now();
  await assert.rejects(
    locateInPage("css", "b.never", { timeout: 150 }),
    /no such element/,
  );
  assert.ok(Date.now() - started >= 140, "should have waited out the budget");
});

test("pageErrorCode maps page-side messages back to BiDi codes", () => {
  assert.equal(pageErrorCode(new Error("no such element: css=b")), "no such element");
  assert.equal(pageErrorCode(new Error("invalid argument: js= ...")), "invalid argument");
  assert.equal(pageErrorCode(new Error("boom")), "unknown error");
});
