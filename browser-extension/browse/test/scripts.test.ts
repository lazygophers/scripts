import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  declContentClear,
  declContentSetRules,
  userScriptsList,
  userScriptsRegister,
  userScriptsReset,
  userScriptsUnregister,
  userScriptsDisconnect,
  userScriptsReconnect,
  userScriptsWorld,
} from "../src/handlers/scripts.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

afterEach(clearChrome);

function usWorld(): {
  registered: Any[];
  unregistered: unknown[];
  reset: number;
  configured: Any[];
  store: Any;
} {
  const state = { registered: [] as Any[], unregistered: [] as unknown[], reset: 0, configured: [] as Any[] };
  installChrome({
    userScripts: {
      register: async (scripts: Any[]) => void state.registered.push(...scripts),
      getScripts: async () => state.registered,
      unregister: async (id: unknown) => void state.unregistered.push(id),
      reset: async () => {
        state.reset += 1;
        state.registered.length = 0; // reset 是浏览器级的全清
      },
      configureWorld: async (d: Any) => void state.configured.push(d),
    },
    policy: { confirm: async () => {} },
  });
  const store = storageMock().data;
  return Object.assign(state, { store });
}

function declWorld(): { removed: unknown[]; added: Any[] } {
  const state = { removed: [] as unknown[], added: [] as Any[] };
  installChrome({
    declarativeContent: {
      PageUrlMatcher: class {
        opts: Any;
        constructor(opts: Any) {
          this.opts = opts;
        }
      },
      ShowAction: class {},
      onPageChanged: {
        removeRules: async (id: unknown) => void state.removed.push(id),
        addRules: async (rules: Any[]) => void state.added.push(...rules),
      },
    },
  });
  return state;
}

describe("userScripts", () => {
  it("registers a script with defaults and optional world/allFrames", async () => {
    const s = usWorld();
    assert.deepEqual(
      await userScriptsRegister({ name: "n1", js: "1+1", matches: ["https://a.example/*"] }),
      { registered: "n1" },
    );
    assert.deepEqual(s.registered[0], {
      id: "n1",
      js: [{ code: "1+1" }],
      matches: ["https://a.example/*"],
      world: "USER_SCRIPT",
    });
    await userScriptsRegister({
      name: "n2",
      js: "2",
      matches: ["https://b.example/*"],
      world: "MAIN",
      allFrames: true,
    });
    assert.equal(s.registered[1]?.world, "MAIN");
    assert.equal(s.registered[1]?.allFrames, true);
    assert.deepEqual(await userScriptsList(), { scripts: s.registered });
  });

  it("validates register arguments", async () => {
    usWorld();
    await rejectsWith(() => userScriptsRegister({ js: "1", matches: ["a"] }), "invalid argument");
    await rejectsWith(() => userScriptsRegister({ name: "n", matches: ["a"] }), "invalid argument");
    await rejectsWith(() => userScriptsRegister({ name: "n", js: "1" }), "invalid argument");
    await rejectsWith(() => userScriptsRegister({ name: "n", js: "1", matches: [] }), "invalid argument");
    await rejectsWith(
      () => userScriptsRegister({ name: "n", js: "1", matches: [1] }),
      "invalid argument",
    );
    await rejectsWith(
      () => userScriptsRegister({ name: "n", js: "1", matches: ["a"], world: "NOPE" }),
      "invalid argument",
    );
  });

  it("unregisters by id and resets everything", async () => {
    const s = usWorld();
    assert.deepEqual(await userScriptsUnregister({ name: "n1" }), { unregistered: "n1" });
    assert.deepEqual(s.unregistered, [{ id: "n1" }]);
    assert.deepEqual(await userScriptsReset(), { reset: true });
    assert.equal(s.reset, 1);
    await rejectsWith(() => userScriptsUnregister({}), "invalid argument");
  });

  it("userScriptsDisconnect resets what is in the persisted table, and nothing when idle", async () => {
    const s = usWorld();
    await userScriptsRegister({ name: "mine", js: "1", matches: ["https://a.example/*"] });
    await userScriptsDisconnect(); // bridge 断开：撤销（reset 是浏览器级的全清）
    assert.equal(s.reset, 1);
    s.reset = 0;
    s.store["browse:userscripts"] = []; // 表也空了（或从未注册过）
    await userScriptsDisconnect(); // 表为空：不碰 API
    assert.equal(s.reset, 0);
  });

  it("persists registrations and restores them on reconnect", async () => {
    const s = usWorld();
    await userScriptsRegister({ name: "mine", js: "1", matches: ["https://a.example/*"] });
    assert.deepEqual(s.store["browse:userscripts"], [
      { id: "mine", js: "1", matches: ["https://a.example/*"], world: "USER_SCRIPT" },
    ]);
    await userScriptsDisconnect();
    assert.deepEqual(s.registered, []); // reset 清了浏览器侧
    await userScriptsReconnect();
    assert.deepEqual(s.registered, [
      { id: "mine", js: [{ code: "1" }], matches: ["https://a.example/*"], world: "USER_SCRIPT" },
    ]);
    assert.deepEqual(s.store["browse:userscripts"], [
      { id: "mine", js: "1", matches: ["https://a.example/*"], world: "USER_SCRIPT" },
    ]);
  });

  it("unregister and reset keep the persisted table in sync", async () => {
    const s = usWorld();
    await userScriptsRegister({ name: "n1", js: "1", matches: ["https://a.example/*"] });
    await userScriptsRegister({ name: "n2", js: "2", matches: ["https://b.example/*"], world: "MAIN", allFrames: true });
    await userScriptsUnregister({ name: "n1" });
    assert.deepEqual(s.store["browse:userscripts"], [
      { id: "n2", js: "2", matches: ["https://b.example/*"], world: "MAIN", allFrames: true },
    ]);
    await userScriptsReset();
    assert.deepEqual(s.store["browse:userscripts"], []); // 显式 reset：重连不恢复
  });

  it("restores a table left by a previous SW; disconnect cleans up its leftovers", async () => {
    const s = usWorld();
    s.store["browse:userscripts"] = [
      { id: "old", js: "9", matches: ["https://x.example/*"], world: "MAIN" },
    ]; // SW 重启：内存 registeredIds 空，表还在
    await userScriptsDisconnect();
    assert.equal(s.reset, 1); // 瑕疵修复：残留照样被清
    await userScriptsReconnect();
    assert.deepEqual(s.registered, [
      { id: "old", js: [{ code: "9" }], matches: ["https://x.example/*"], world: "MAIN" },
    ]);
  });

  it("a failed re-registration is logged and does not block the rest", async () => {
    const s = usWorld();
    s.store["browse:userscripts"] = [
      { id: "bad", js: "1", matches: [], world: "USER_SCRIPT" }, // matches 空，mock 不校验，手动让它抛
    ];
    installChrome({
      userScripts: {
        register: async (scripts: Any[]) => {
          if ((scripts[0] as Any).id === "bad") throw new Error("boom");
          void s.registered.push(...scripts);
        },
      },
    });
    await userScriptsReconnect(); // 不抛
    assert.deepEqual(s.registered, []);
  });

  it("configures the USER_SCRIPT world", async () => {
    const s = usWorld();
    assert.deepEqual(await userScriptsWorld({ messaging: true }), { messaging: true, csp: null });
    assert.deepEqual(s.configured[0], { messaging: true });
    assert.deepEqual(await userScriptsWorld({ messaging: false, csp: "default-src" }), {
      messaging: false,
      csp: "default-src",
    });
    assert.deepEqual(s.configured[1], { messaging: false, csp: "default-src" });
  });

  it("refuses when the browser has no userScripts API", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => userScriptsRegister({ name: "n", js: "1", matches: ["a"] }), "unsupported operation");
    await rejectsWith(() => userScriptsList(), "unsupported operation");
    await rejectsWith(() => userScriptsReset(), "unsupported operation");
    await rejectsWith(() => userScriptsWorld({}), "unsupported operation");
  });
});

describe("declarativeContent", () => {
  it("replaces the whole rule set", async () => {
    const d = declWorld();
    assert.deepEqual(
      await declContentSetRules({ rules: [{ urlPrefix: "https://a.example/" }, { urlMatches: "*.b.example" }] }),
      { rules: 2 },
    );
    assert.deepEqual(d.removed, [undefined]);
    assert.equal(d.added.length, 2);
    assert.equal(d.added[0]?.id, "rule-1");
  });

  it("validates rules", async () => {
    declWorld();
    await rejectsWith(() => declContentSetRules({}), "invalid argument");
    await rejectsWith(() => declContentSetRules({ rules: [] }), "invalid argument");
    await rejectsWith(() => declContentSetRules({ rules: [{}] }), "invalid argument");
    await rejectsWith(() => declContentSetRules({ rules: [{ urlPrefix: 3 }] }), "invalid argument");
  });

  it("clears rules", async () => {
    const d = declWorld();
    assert.deepEqual(await declContentClear(), { cleared: true });
    assert.deepEqual(d.removed, [undefined]);
  });

  it("refuses when the API is missing", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => declContentSetRules({ rules: [{ urlPrefix: "x" }] }), "unsupported operation");
    await rejectsWith(() => declContentClear(), "unsupported operation");
  });
});
