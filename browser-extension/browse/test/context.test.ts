import assert from "node:assert/strict";
import test from "node:test";
import {
  contextUrl,
  formatContext,
  globToRegExp,
  parseContext,
  requireApi,
  resolveContext,
} from "../src/handlers/context.ts";
import { resetOwnership } from "../src/handlers/ownership.ts";
import { clearChrome, installChrome, ownWorld, rejectsWith } from "./mock.ts";

/** 自己的组（500）里的三个 tab + 组外一个别人的 tab（9）。 */
function withOwnTabs(): void {
  ownWorld([
    { id: 1, url: "https://a.test/login", active: false, groupId: 500 },
    { id: 2, url: "https://b.test/orders/7", active: true, groupId: 500 },
    { id: 3, url: "https://b.test/orders/8", active: false, groupId: 500 },
    { id: 9, url: "https://c.test/private", active: false },
  ]);
}

test.afterEach(() => {
  resetOwnership();
  clearChrome();
});

test("context id wins over matchUrl and over the active tab", async () => {
  withOwnTabs();
  assert.deepEqual(await resolveContext({ context: "3", matchUrl: "*a.test*" }), {
    tabId: 3,
    frameId: undefined,
  });
});

test("matchUrl wins over the active tab when it hits exactly one", async () => {
  withOwnTabs();
  assert.deepEqual(await resolveContext({ matchUrl: "https://a.test/*" }), {
    tabId: 1,
    frameId: undefined,
  });
});

test("own scope: a glob that only matches a foreign tab is no hit, naming the two entrances", async () => {
  withOwnTabs();
  const err = await rejectsWith(
    () => resolveContext({ matchUrl: "*c.test*" }),
    "no such frame",
  );
  assert.match(err.message, /no owned page matches/);
  assert.match(err.message, /browse tab adopt/);
});

test("all scope (adopt) still searches every tab in the browser", async () => {
  withOwnTabs();
  assert.deepEqual(await resolveContext({ matchUrl: "*c.test*" }, "all"), {
    tabId: 9,
    frameId: undefined,
  });
});

test("an ambiguous matchUrl is an error naming every hit, not a coin flip", async () => {
  withOwnTabs();
  const err = await rejectsWith(
    () => resolveContext({ matchUrl: "https://b.test/orders/*" }),
    "invalid argument",
  );
  assert.match(err.message, /matches 2 contexts/);
  assert.match(err.message, /2=https:\/\/b.test\/orders\/7/);
});

test("own active fallback picks the active owned tab", async () => {
  withOwnTabs();
  assert.deepEqual(await resolveContext({}), { tabId: 2, frameId: undefined });
});

test("no active owned tab but exactly one owned tab resolves to it", async () => {
  ownWorld([{ id: 4, url: "https://a.test/x", active: false, groupId: 500 }]);
  assert.deepEqual(await resolveContext({}), { tabId: 4, frameId: undefined });
});

test("no owned tabs at all is an error pointing at tab open / adopt", async () => {
  ownWorld([]);
  const err = await rejectsWith(() => resolveContext({}), "no such frame");
  assert.match(err.message, /browse tab open/);
});

test("frame context ids keep the frame id", () => {
  assert.deepEqual(parseContext("12.3"), { tabId: 12, frameId: 3 });
  assert.deepEqual(parseContext("12"), { tabId: 12, frameId: undefined });
  assert.throws(() => parseContext("nope"), /bad context id/);
});

test("formatContext is the inverse of parseContext, and frame 0 is the tab", () => {
  assert.equal(formatContext(12), "12");
  assert.equal(formatContext(12, 3), "12.3");
  assert.equal(formatContext(12, 0), "12");
  assert.deepEqual(parseContext(formatContext(12, 3)), { tabId: 12, frameId: 3 });
});

test("lg:context.url answers with the page the command would land on", async () => {
  withOwnTabs();
  // This is what the daemon asks before applying deny_domains to input.* /
  // script.*, whose params carry no url at all.
  assert.deepEqual(await contextUrl({}), { url: "https://b.test/orders/7" });
  assert.deepEqual(await contextUrl({ context: "1" }), { url: "https://a.test/login" });
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
});

test("a malformed frame part is a bad context id", async () => {
  withOwnTabs();
  const err = await rejectsWith(() => resolveContext({ context: "1.frame" }), "no such frame");
  assert.match(err.message, /bad context id/);
});

test("all-scope matchUrl reports its own miss message; no active tab also refuses", async () => {
  withOwnTabs();
  const miss = await rejectsWith(
    () => resolveContext({ matchUrl: "https://nowhere.test/*" }, "all"),
    "no such frame",
  );
  assert.match(miss.message, /no browsing context matches/);

  // all scope 的活动页兜底查 currentWindow 的 active tab；没有就拒
  ownWorld([], { registry: [] });
  const { tabs } = { tabs: undefined };
  void tabs;
  installChrome({
    tabs: {
      query: async () => [],
      group: async () => 1,
    },
    tabGroups: { query: async () => [] },
  });
  await rejectsWith(() => resolveContext({}, "all"), "no such frame");
});

test("multiple matchUrl hits list every candidate with its url", async () => {
  withOwnTabs();
  const err = await rejectsWith(
    () => resolveContext({ matchUrl: "https://b.test/*" }),
    "invalid argument",
  );
  assert.match(err.message, /matches 2 contexts/);
  assert.match(err.message, /b\.test/);
});

test("a matchUrl hit whose tab has no id is refused", async () => {
  ownWorld([{ url: "https://a.test/login", active: true, groupId: 500 }]);
  await rejectsWith(() => resolveContext({ matchUrl: "https://a.test/*" }), "no such frame");
});
