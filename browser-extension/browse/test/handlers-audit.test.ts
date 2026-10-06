import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { auditClear, auditRead } from "../src/handlers/audit.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

const ENTRIES = [
  { ts: "2026-01-01T00:00:00Z", method: "script.evaluate", domain: "a.test", action: null, result: "success", ms: 1 },
  { ts: "2026-01-01T00:00:01Z", method: "input.click", domain: "b.test", action: null, result: "error", ms: 2 },
  { ts: "2026-01-01T00:00:02Z", method: "cookies.getCookies", domain: "c.test", action: "readCookies", result: "success", ms: 3 },
];

afterEach(clearChrome);

function auditWorld(entries: unknown = ENTRIES): void {
  installChrome({ storage: { local: { get: async () => ({ "browse:audit": entries }), set: async () => {}, remove: async () => {} } } });
}

describe("audit.read", () => {
  it("returns every entry by default and honours a limit", async () => {
    auditWorld();
    assert.deepEqual((await auditRead({})).entries, ENTRIES);
    const last = await auditRead({ limit: 1 });
    assert.deepEqual(last.entries, [ENTRIES[2]]);
    assert.deepEqual((await auditRead({ limit: 99 })).entries, ENTRIES);
  });

  it("returns nothing when the log is missing or malformed", async () => {
    installChrome({ storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } } });
    assert.deepEqual((await auditRead({})).entries, []);
    auditWorld("not a list");
    assert.deepEqual((await auditRead({})).entries, []);
  });

  it("validates the limit", async () => {
    auditWorld();
    await rejectsWith(() => auditRead({ limit: -1 }), "invalid argument");
    await rejectsWith(() => auditRead({ limit: 1.5 }), "invalid argument");
    await rejectsWith(() => auditRead({ limit: "3" }), "invalid argument");
  });
});

describe("audit.clear", () => {
  it("reports how many entries were dropped and empties the log", async () => {
    const store: Any = { "browse:audit": ENTRIES };
    installChrome({ storage: { local: { get: async () => store, set: async () => {}, remove: async (key: string) => void delete store[key] } } });
    assert.deepEqual(await auditClear(), { cleared: 3 });
    assert.equal("browse:audit" in store, false);
  });
});
