import assert from "node:assert/strict";
import test from "node:test";
import {
  declContentClear,
  declContentSetRules,
  userScriptsList,
  userScriptsRegister,
  userScriptsReset,
  userScriptsUnregister,
  userScriptsWorld,
} from "../src/handlers/scripts.ts";
import { setConfirmHook } from "../src/handlers/confirm.ts";
import { installChrome, ownSession, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

function setup() {
  const calls: Any = { registered: [], configured: null, removed: null, addedRules: [] };
  class PageUrlMatcher {
    init: Any;
    constructor(init: Any) {
      this.init = init;
    }
  }
  class ShowAction {}
  installChrome({
    ...ownSession(),
    userScripts: {
      register: async (specs: Any[]) => {
        calls.registered.push(...specs);
      },
      getScripts: async () => [{ id: "one" }, { id: "two" }],
      unregister: async (f: Any) => {
        calls.unregistered = f.id;
      },
      reset: async () => {
        calls.reset = true;
      },
      configureWorld: async (c: Any) => {
        calls.configured = c;
      },
    },
    declarativeContent: {
      PageUrlMatcher,
      ShowAction,
      onPageChanged: {
        removeRules: async (ids: unknown) => {
          calls.removed = ids;
        },
        addRules: async (rules: Any[]) => {
          calls.addedRules.push(...rules);
        },
      },
    },
  });
  setConfirmHook(async () => true);
  return { calls, PageUrlMatcher, ShowAction };
}

test("userScriptsRegister validates then registers with defaults", async () => {
  const { calls } = setup();
  await rejectsWith(
    () => userScriptsRegister({ name: "s", js: "x", matches: [] }),
    "invalid argument",
  );
  await rejectsWith(
    () => userScriptsRegister({ name: "s", js: "x", matches: ["a", 1] }),
    "invalid argument",
  );
  await rejectsWith(
    () => userScriptsRegister({ name: "s", js: "x", matches: ["a"], world: "ISOLATED" }),
    "invalid argument",
  );
  assert.deepEqual(calls.registered, []);

  assert.deepEqual(
    await userScriptsRegister({ name: "s", js: "console.log(1)", matches: ["https://a.test/*"] }),
    { registered: "s" },
  );
  assert.deepEqual(calls.registered, [{
    id: "s",
    js: [{ code: "console.log(1)" }],
    matches: ["https://a.test/*"],
    world: "USER_SCRIPT",
  }]);

  assert.deepEqual(
    await userScriptsRegister({ name: "m", js: "x", matches: ["a"], world: "MAIN", allFrames: true }),
    { registered: "m" },
  );
  assert.equal(calls.registered[1].world, "MAIN");
  assert.equal(calls.registered[1].allFrames, true);
});

test("list / unregister / reset round-trip chrome.userScripts", async () => {
  const { calls } = setup();
  assert.deepEqual(await userScriptsList(), { scripts: [{ id: "one" }, { id: "two" }] });
  assert.deepEqual(await userScriptsUnregister({ name: "one" }), { unregistered: "one" });
  assert.equal(calls.unregistered, "one");
  assert.deepEqual(await userScriptsReset(), { reset: true });
  assert.equal(calls.reset, true);
});

test("userScriptsWorld configures messaging and csp", async () => {
  const { calls } = setup();
  assert.deepEqual(await userScriptsWorld({ messaging: true, csp: "script-src 'self'" }), {
    messaging: true,
    csp: "script-src 'self'",
  });
  assert.deepEqual(calls.configured, { messaging: true, csp: "script-src 'self'" });
  assert.deepEqual(await userScriptsWorld({}), { messaging: false, csp: null });
  assert.deepEqual(calls.configured, { messaging: false });
});

test("declContentSetRules replaces the whole rule set", async () => {
  const setup2 = setup();
  const { calls } = setup2;
  await rejectsWith(() => declContentSetRules({ rules: [] }), "invalid argument");
  await rejectsWith(
    () => declContentSetRules({ rules: [{}] }),
    "invalid argument",
  );
  assert.deepEqual(calls.addedRules, []);

  assert.deepEqual(
    await declContentSetRules({ rules: [{ urlPrefix: "https://a.test/" }, { urlMatches: "*b.test*" }] }),
    { rules: 2 },
  );
  assert.equal(calls.removed, undefined);
  assert.equal(calls.addedRules.length, 2);
  assert.equal(calls.addedRules[0].id, "rule-1");
  assert.equal(calls.addedRules[0].conditions[0].init.urlPrefix, "https://a.test/");
  assert.equal(calls.addedRules[1].conditions[0].init.urlMatches, "*b.test*");
  const { ShowAction } = setup2;
});

test("declContentClear removes every rule", async () => {
  const { calls } = setup();
  assert.deepEqual(await declContentClear(), { cleared: true });
  assert.equal(calls.removed, undefined);
  assert.deepEqual(calls.addedRules, []);
});
