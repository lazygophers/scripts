import assert from "node:assert/strict";
import test from "node:test";
import {
  globToRegExp,
  parseContext,
  requireApi,
  resolveContext,
} from "../src/handlers/context.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

const TABS = [
  { id: 1, url: "https://a.test/login", active: false, windowId: 10 },
  { id: 2, url: "https://b.test/orders/7", active: true, windowId: 10 },
  { id: 3, url: "https://b.test/orders/8", active: false, windowId: 10 },
];

function withTabs(): void {
  installChrome({
    tabs: {
      query: async (q: { active?: boolean }) =>
        q.active === true ? TABS.filter((t) => t.active) : TABS,
    },
  });
}

test("context id wins over matchUrl and over the active tab", async () => {
  withTabs();
  assert.deepEqual(await resolveContext({ context: "3", matchUrl: "*a.test*" }), {
    tabId: 3,
    frameId: undefined,
  });
  clearChrome();
});

test("matchUrl wins over the active tab when it hits exactly one", async () => {
  withTabs();
  assert.deepEqual(await resolveContext({ matchUrl: "https://a.test/*" }), {
    tabId: 1,
    frameId: undefined,
  });
  clearChrome();
});

test("an ambiguous matchUrl is an error naming every hit, not a coin flip", async () => {
  withTabs();
  const err = await rejectsWith(
    () => resolveContext({ matchUrl: "https://b.test/orders/*" }),
    "invalid argument",
  );
  assert.match(err.message, /matches 2 contexts/);
  assert.match(err.message, /2=https:\/\/b.test\/orders\/7/);
  clearChrome();
});

test("matchUrl with no hit is no such frame", async () => {
  withTabs();
  await rejectsWith(() => resolveContext({ matchUrl: "https://c.test/*" }), "no such frame");
  clearChrome();
});

test("no context and no matchUrl falls back to the active tab", async () => {
  withTabs();
  assert.deepEqual(await resolveContext({}), { tabId: 2, frameId: undefined });
  clearChrome();
});

test("frame context ids keep the frame id", () => {
  assert.deepEqual(parseContext("12.3"), { tabId: 12, frameId: 3 });
  assert.deepEqual(parseContext("12"), { tabId: 12, frameId: undefined });
  assert.throws(() => parseContext("nope"), /bad context id/);
});

test("glob is anchored and only * and ? are special", () => {
  assert.ok(globToRegExp("*/api/*").test("https://x.test/api/orders"));
  assert.ok(!globToRegExp("*/api/*").test("https://x.test/apx/orders"));
  assert.ok(globToRegExp("https://a.b/c?d").test("https://a.b/cXd"));
  // A dot in the glob is a literal dot, not "any character".
  assert.ok(!globToRegExp("https://a.test/").test("https://aXtest/"));
});

test("a missing chrome namespace is an explicit unsupported operation", async () => {
  installChrome({ tabs: {} });
  const err = await rejectsWith(
    async () => requireApi("downloads", "downloading files"),
    "unsupported operation",
  );
  assert.match(err.message, /chrome.downloads is not available/);
  assert.doesNotThrow(() => requireApi("tabs", "listing tabs"));
  clearChrome();
});
