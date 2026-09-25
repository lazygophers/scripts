import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { permissionsContains, permissionsGetAll } from "../src/handlers/perms.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

/** permissions 是只读面：不碰策略，所以这里不需要 storage / confirm 的桩。 */
function permissionsApi() {
  const calls: unknown[] = [];
  return {
    calls,
    api: {
      getAll: async () => ({ permissions: ["tabs"], origins: ["https://e.test/*"] }),
      contains: async (arg: unknown) => {
        calls.push(arg);
        return true;
      },
    },
  };
}

afterEach(clearChrome);

describe("permissionsGetAll", () => {
  it("refuses when the browser has no permissions API", async () => {
    installChrome({});
    await rejectsWith(() => permissionsGetAll(), "unsupported operation");
  });

  it("returns what the browser granted", async () => {
    const { api } = permissionsApi();
    installChrome({ permissions: api });
    assert.deepEqual(await permissionsGetAll(), {
      permissions: ["tabs"],
      origins: ["https://e.test/*"],
    });
  });
});

describe("permissionsContains", () => {
  it("refuses when the browser has no permissions API", async () => {
    installChrome({});
    await rejectsWith(() => permissionsContains({ permissions: ["tabs"] }), "unsupported operation");
  });

  it("needs permissions and/or origins", async () => {
    const { api } = permissionsApi();
    installChrome({ permissions: api });
    await rejectsWith(() => permissionsContains({}), "invalid argument");
  });

  it("rejects permissions that are not a list of strings", async () => {
    const { api } = permissionsApi();
    installChrome({ permissions: api });
    await rejectsWith(() => permissionsContains({ permissions: "tabs" }), "invalid argument");
    await rejectsWith(() => permissionsContains({ permissions: ["tabs", 1] }), "invalid argument");
  });

  it("rejects origins that are not a list of strings", async () => {
    const { api } = permissionsApi();
    installChrome({ permissions: api });
    await rejectsWith(() => permissionsContains({ origins: "https://e.test/*" }), "invalid argument");
    await rejectsWith(() => permissionsContains({ origins: [null] }), "invalid argument");
  });

  it("forwards only the fields that were given", async () => {
    const { calls, api } = permissionsApi();
    installChrome({ permissions: api });
    assert.deepEqual(await permissionsContains({ permissions: ["tabs"] }), { contains: true });
    assert.deepEqual(calls[0], { permissions: ["tabs"] });
  });

  it("accepts both fields together", async () => {
    const { calls, api } = permissionsApi();
    installChrome({ permissions: api });
    await permissionsContains({ permissions: ["tabs"], origins: ["https://e.test/*"] });
    assert.deepEqual(calls[0], { permissions: ["tabs"], origins: ["https://e.test/*"] });
  });

  it("treats an empty list as given — it is a real question, not a missing argument", async () => {
    const { calls, api } = permissionsApi();
    installChrome({ permissions: api });
    await permissionsContains({ permissions: [] });
    assert.deepEqual(calls[0], { permissions: [] });
  });
});
