import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { idleState, searchQuery, systemInfo, topSitesList } from "../src/handlers/info.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

/** 只读信息类：不写状态、不碰页面，所以不需要 storage / confirm 的桩。 */
afterEach(clearChrome);

describe("topSitesList", () => {
  it("refuses when the browser has no topSites API", async () => {
    installChrome({});
    await rejectsWith(() => topSitesList(), "unsupported operation");
  });

  it("wraps the list under sites", async () => {
    installChrome({ topSites: { get: async () => [{ url: "https://e.test/", title: "E" }] } });
    assert.deepEqual(await topSitesList(), {
      sites: [{ url: "https://e.test/", title: "E" }],
    });
  });
});

describe("searchQuery", () => {
  function searchApi() {
    const calls: unknown[] = [];
    return { calls, api: { query: async (arg: unknown) => void calls.push(arg) } };
  }

  it("refuses when the browser has no search API", async () => {
    installChrome({});
    await rejectsWith(() => searchQuery({ text: "x" }), "unsupported operation");
  });

  it("needs a text", async () => {
    const { api } = searchApi();
    installChrome({ search: api });
    await rejectsWith(() => searchQuery({}), "invalid argument");
  });

  it("treats an empty text as missing", async () => {
    const { api } = searchApi();
    installChrome({ search: api });
    await rejectsWith(() => searchQuery({ text: "" }), "invalid argument");
  });

  it("rejects a non-string text", async () => {
    const { api } = searchApi();
    installChrome({ search: api });
    await rejectsWith(() => searchQuery({ text: 1 }), "invalid argument");
  });

  it("searches in the current tab when no disposition is given", async () => {
    const { calls, api } = searchApi();
    installChrome({ search: api });
    assert.deepEqual(await searchQuery({ text: "关键词" }), { searched: "关键词" });
    assert.deepEqual(calls[0], { text: "关键词" });
  });

  it("forwards a known disposition", async () => {
    const { calls, api } = searchApi();
    installChrome({ search: api });
    await searchQuery({ text: "x", disposition: "NEW_TAB" });
    assert.deepEqual(calls[0], { text: "x", disposition: "NEW_TAB" });
  });

  it("rejects a disposition outside the three Chrome accepts", async () => {
    const { calls, api } = searchApi();
    installChrome({ search: api });
    await rejectsWith(() => searchQuery({ text: "x", disposition: "POPUP" }), "invalid argument");
    assert.equal(calls.length, 0);
  });
});

describe("idleState", () => {
  function idleApi(state = "active") {
    const calls: unknown[] = [];
    return {
      calls,
      api: {
        queryState: async (arg: unknown) => {
          calls.push(arg);
          return state;
        },
      },
    };
  }

  it("refuses when the browser has no idle API", async () => {
    installChrome({});
    await rejectsWith(() => idleState({}), "unsupported operation");
  });

  it("defaults the threshold to 60 seconds", async () => {
    const { calls, api } = idleApi("idle");
    installChrome({ idle: api });
    assert.deepEqual(await idleState({}), { state: "idle" });
    assert.deepEqual(calls[0], 60);
  });

  it("accepts a numeric string threshold (Number coercion is deliberate)", async () => {
    const { calls, api } = idleApi();
    installChrome({ idle: api });
    await idleState({ threshold: "120" });
    assert.deepEqual(calls[0], 120);
  });

  it("rejects a threshold below Chrome's 15 second floor", async () => {
    const { api } = idleApi();
    installChrome({ idle: api });
    await rejectsWith(() => idleState({ threshold: 14 }), "invalid argument");
  });

  it("rejects a threshold above one hour", async () => {
    const { api } = idleApi();
    installChrome({ idle: api });
    await rejectsWith(() => idleState({ threshold: 3601 }), "invalid argument");
  });

  it("rejects a non-integer threshold", async () => {
    const { api } = idleApi();
    installChrome({ idle: api });
    await rejectsWith(() => idleState({ threshold: 60.5 }), "invalid argument");
    await rejectsWith(() => idleState({ threshold: "六十" }), "invalid argument");
  });
});

describe("systemInfo", () => {
  function systemApi(): Any {
    return {
      cpu: { getInfo: async () => ({ archName: "arm64" }) },
      memory: { getInfo: async () => ({ capacity: 8 }) },
      display: { getInfo: async () => [{ id: "1" }] },
      storage: { getInfo: async () => [{ id: "s" }] },
    };
  }

  it("returns all four parts by default", async () => {
    installChrome({ system: systemApi() });
    assert.deepEqual(await systemInfo({}), {
      cpu: { archName: "arm64" },
      memory: { capacity: 8 },
      display: [{ id: "1" }],
      storage: [{ id: "s" }],
    });
  });

  it("returns only the parts that were asked for", async () => {
    installChrome({ system: systemApi() });
    assert.deepEqual(await systemInfo({ parts: ["memory"] }), { memory: { capacity: 8 } });
  });

  it("rejects a parts list that is not a list", async () => {
    installChrome({ system: systemApi() });
    await rejectsWith(() => systemInfo({ parts: "cpu" }), "invalid argument");
  });

  it("rejects an unknown part name", async () => {
    installChrome({ system: systemApi() });
    await rejectsWith(() => systemInfo({ parts: ["cpu", "gpu"] }), "invalid argument");
  });

  it("refuses per part when that namespace is missing", async () => {
    // system.cpu 在，system.memory 不在：只问 memory 就该明确拒绝
    installChrome({ system: { cpu: { getInfo: async () => ({}) } } });
    await rejectsWith(() => systemInfo({ parts: ["memory"] }), "unsupported operation");
  });

  it("an empty parts list asks for nothing and returns nothing", async () => {
    installChrome({ system: systemApi() });
    assert.deepEqual(await systemInfo({ parts: [] }), {});
  });
});
