import assert from "node:assert/strict";
import test from "node:test";
import { inputClick, inputKey, inputScroll, inputType } from "../src/handlers/input.ts";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import { clearChrome, installChrome, page, rejectsWith, scriptingMock } from "./mock.ts";
import type { JSDOM } from "jsdom";

type Any = Record<string, unknown>;

function setup(html: string): { dom: JSDOM; calls: Any[] } {
  const dom = page(html);
  const scripting = scriptingMock();
  installChrome({
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true }],
      get: async () => ({ id: 7, url: "https://a.test/" }),
    },
    scripting,
  });
  return { dom, calls: scripting.calls };
}

test("click fires the whole synthetic sequence and reports isTrusted false", async () => {
  const { dom } = setup(`<button id="go">Go</button>`);
  const seen: string[] = [];
  const button = dom.window.document.querySelector("#go")!;
  for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
    button.addEventListener(type, (e) => seen.push(`${e.type}:${e.isTrusted}`));
  }

  const result = (await inputClick({ selector: "#go" })) as Any;
  assert.deepEqual(seen, [
    "pointerdown:false",
    "mousedown:false",
    "mouseup:false",
    "click:false",
  ]);
  assert.equal(result["lg:isTrusted"], false);
  assert.deepEqual(result.element, ["<button#go>"]);
  clearChrome();
});

test("a disabled element is refused at the click, not at location time", async () => {
  setup(`<button id="go" disabled>Go</button>`);
  const err = await rejectsWith(() => inputClick({ selector: "#go" }), "invalid argument");
  assert.match(err.message, /element is disabled/);
  clearChrome();
});

test("aria-disabled counts as disabled", async () => {
  setup(`<div id="go" role="button" aria-disabled="true">Go</div>`);
  await rejectsWith(() => inputClick({ selector: "#go" }), "invalid argument");
  clearChrome();
});

test("click without a selector is rejected up front", async () => {
  setup("<p>x</p>");
  await rejectsWith(() => inputClick({}), "invalid argument");
  clearChrome();
});

test("a missing element surfaces as no such element, not an empty success", async () => {
  setup("<p>x</p>");
  await rejectsWith(() => inputClick({ selector: "#nope", wait: false }), "no such element");
  clearChrome();
});

test("all: true clicks every match", async () => {
  const { dom } = setup(`<button class="x">a</button><button class="x">b</button>`);
  let clicks = 0;
  for (const el of dom.window.document.querySelectorAll(".x")) {
    el.addEventListener("click", () => (clicks += 1));
  }
  const result = (await inputClick({ selector: ".x", all: true })) as Any;
  assert.equal(clicks, 2);
  assert.equal(result.count, 2);
  clearChrome();
});

test("type sets the value and fires input then change", async () => {
  const { dom } = setup(`<input id="u" value="old">`);
  const input = dom.window.document.querySelector("#u") as HTMLInputElement;
  const seen: string[] = [];
  for (const type of ["input", "change"]) {
    input.addEventListener(type, (e) => seen.push(`${e.type}:${e.isTrusted}`));
  }

  await inputType({ selector: "#u", text: "hello" });
  assert.equal(input.value, "hello");
  // clear defaults on, so the old value is wiped with its own input event.
  assert.deepEqual(seen, ["input:false", "input:false", "change:false"]);
  clearChrome();
});

test("type respects clear: false", async () => {
  const { dom } = setup(`<input id="u" value="old">`);
  await inputType({ selector: "#u", text: "new", clear: false });
  assert.equal((dom.window.document.querySelector("#u") as HTMLInputElement).value, "new");
  clearChrome();
});

test("a readonly field is refused", async () => {
  setup(`<input id="u" readonly>`);
  const err = await rejectsWith(
    () => inputType({ selector: "#u", text: "x" }),
    "invalid argument",
  );
  assert.match(err.message, /readonly/);
  clearChrome();
});

test("type into a contenteditable writes its text", async () => {
  const { dom } = setup(`<div id="c" contenteditable="true">old</div>`);
  await inputType({ selector: "#c", text: "new" });
  assert.equal(dom.window.document.querySelector("#c")?.textContent, "new");
  clearChrome();
});

test("type rejects a non-string text", async () => {
  setup(`<input id="u">`);
  await rejectsWith(() => inputType({ selector: "#u", text: 5 }), "invalid argument");
  clearChrome();
});

test("key dispatches keydown/keyup with modifiers, and keypress only for a character", async () => {
  const { dom } = setup(`<input id="u">`);
  const input = dom.window.document.querySelector("#u")!;
  const seen: string[] = [];
  for (const type of ["keydown", "keypress", "keyup"]) {
    input.addEventListener(type, (e) => {
      const ev = e as KeyboardEvent;
      seen.push(`${ev.type}:${ev.key}:${ev.ctrlKey}`);
    });
  }

  await inputKey({ selector: "#u", key: "Enter" });
  assert.deepEqual(seen, ["keydown:Enter:false", "keyup:Enter:false"]);

  seen.length = 0;
  await inputKey({ selector: "#u", key: "a", ctrl: true });
  assert.deepEqual(seen, ["keydown:a:true", "keypress:a:true", "keyup:a:true"]);
  clearChrome();
});

test("key without a selector goes to the focused element", async () => {
  const { dom } = setup(`<input id="u">`);
  const input = dom.window.document.querySelector("#u") as HTMLInputElement;
  input.focus();
  let hits = 0;
  input.addEventListener("keydown", () => (hits += 1));
  const result = (await inputKey({ key: "Escape" })) as Any;
  assert.equal(hits, 1);
  assert.equal(result.count, 1);
  clearChrome();
});

test("key rejects an empty key", async () => {
  setup("<p>x</p>");
  await rejectsWith(() => inputKey({ key: "" }), "invalid argument");
  clearChrome();
});

test("scroll on an element moves that element and reports the position", async () => {
  const { dom } = setup(`<div id="box">tall</div>`);
  const box = dom.window.document.querySelector("#box") as HTMLElement;
  box.scrollBy = function (dx: number, dy: number) {
    this.scrollLeft += dx;
    this.scrollTop += dy;
  } as typeof box.scrollBy;

  const result = (await inputScroll({ selector: "#box", dy: 120 })) as Any;
  assert.deepEqual(result.scrolled, { x: 0, y: 120 });
  clearChrome();
});

test("scroll without a selector scrolls the window", async () => {
  const { dom } = setup("<p>x</p>");
  let called: number[] = [];
  (dom.window as unknown as Any).scrollBy = (dx: number, dy: number) => {
    called = [dx, dy];
  };
  const result = (await inputScroll({ dy: 300 })) as Any;
  assert.deepEqual(called, [0, 300]);
  assert.deepEqual(result.scrolled, { x: 0, y: 0 });
  clearChrome();
});

test("scroll rejects a non-numeric delta", async () => {
  setup("<p>x</p>");
  await rejectsWith(() => inputScroll({ dy: "300" }), "invalid argument");
  clearChrome();
});

test("a js= locator switches the injection to MAIN world, css= stays ISOLATED", async () => {
  const { calls } = setup(`<button id="go">Go</button>`);
  await inputClick({ selector: "js=document.querySelector('#go')" });
  assert.equal(calls[0]?.world, "MAIN");

  calls.length = 0;
  await inputClick({ selector: "#go" });
  assert.equal(calls[0]?.world, "ISOLATED");
  clearChrome();
});

test("the locator is injected as a file before the page function runs", async () => {
  const { calls } = setup(`<button id="go">Go</button>`);
  await inputClick({ selector: "#go" });
  assert.deepEqual(calls[0]?.files, ["page-locate.js"]);
  assert.equal(typeof calls[1]?.func, "function");
  clearChrome();
});

test("typing into something that is not a text field fails loudly", async () => {
  setup(`<div id="d">x</div>`);
  const err = await rejectsWith(
    () => inputType({ selector: "#d", text: "x" }),
    "invalid argument",
  );
  assert.match(err.message, /cannot type into <div#d>/);
  clearChrome();
});

test("a js= locator takes the same confirm as script.evaluate, css= takes none", async () => {
  setup(`<button id="go">Go</button>`);
  const asked: ConfirmRequest[] = [];
  setConfirmHook(async (request) => {
    asked.push(request);
    return true;
  });

  await inputClick({ selector: "#go" });
  assert.deepEqual(asked, [], "css= runs in ISOLATED, so it is not a high-risk action");

  await inputClick({ selector: "js=document.querySelector('#go')" });
  assert.deepEqual(asked, [
    { action: "evalMainWorld", method: "input.click", url: "https://a.test/" },
  ]);

  setConfirmHook(async () => true);
  clearChrome();
});

test("a refused confirm stops a js= locator on every input command", async () => {
  setup(`<input id="u">`);
  setConfirmHook(async () => false);

  for (const call of [
    () => inputClick({ selector: "js=document.querySelector('#u')" }),
    () => inputType({ selector: "js=document.querySelector('#u')", text: "x" }),
    () => inputKey({ selector: "js=document.querySelector('#u')", key: "Enter" }),
    () => inputScroll({ selector: "js=document.querySelector('#u')", dy: 10 }),
  ]) {
    const err = await rejectsWith(call, "lg:user rejected");
    assert.match(err.message, /user denied evalMainWorld for input\./);
  }

  setConfirmHook(async () => true);
  clearChrome();
});
