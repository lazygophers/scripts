import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setEventSink } from "../src/events.ts";
import { gcmDeleteToken, gcmId, gcmToken, listenGcm } from "../src/handlers/gcm.ts";
import type { Event } from "../src/protocol.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

/** instanceID 的桩：记录每次调用的参数，token 由 entity+scope 拼出来便于断言。 */
function instanceIdApi() {
  const calls: { fn: string; arg?: unknown }[] = [];
  return {
    calls,
    api: {
      getID: async () => {
        calls.push({ fn: "getID" });
        return "instance-abc";
      },
      getToken: async (arg: Any) => {
        calls.push({ fn: "getToken", arg });
        return `token:${arg.authorizedEntity}/${arg.scope}`;
      },
      deleteToken: async (arg: unknown) => void calls.push({ fn: "deleteToken", arg }),
    },
  };
}

afterEach(() => {
  clearChrome();
  setEventSink(null);
});

describe("gcmId", () => {
  it("refuses when the browser has no instanceID API", async () => {
    installChrome({});
    await rejectsWith(() => gcmId(), "unsupported operation");
  });

  it("returns the instance id", async () => {
    const { api } = instanceIdApi();
    installChrome({ instanceID: api });
    assert.deepEqual(await gcmId(), { id: "instance-abc" });
  });
});

describe("gcmToken", () => {
  it("refuses when the browser has no instanceID API", async () => {
    installChrome({});
    await rejectsWith(() => gcmToken({ entity: "123" }), "unsupported operation");
  });

  it("needs the sender's project number", async () => {
    const { api } = instanceIdApi();
    installChrome({ instanceID: api });
    await rejectsWith(() => gcmToken({}), "invalid argument");
  });

  it("rejects an empty entity", async () => {
    const { api } = instanceIdApi();
    installChrome({ instanceID: api });
    await rejectsWith(() => gcmToken({ entity: "" }), "invalid argument");
  });

  it("defaults scope to the empty string", async () => {
    const { calls, api } = instanceIdApi();
    installChrome({ instanceID: api });
    assert.deepEqual(await gcmToken({ entity: "123" }), { token: "token:123/" });
    assert.deepEqual(calls[0].arg, { authorizedEntity: "123", scope: "" });
  });

  it("forwards a given scope", async () => {
    const { calls, api } = instanceIdApi();
    installChrome({ instanceID: api });
    await gcmToken({ entity: "123", scope: "GCM" });
    assert.deepEqual(calls[0].arg, { authorizedEntity: "123", scope: "GCM" });
  });

  it("rejects a non-string scope", async () => {
    const { api } = instanceIdApi();
    installChrome({ instanceID: api });
    await rejectsWith(() => gcmToken({ entity: "123", scope: 1 }), "invalid argument");
  });
});

describe("gcmDeleteToken", () => {
  it("needs an entity", async () => {
    const { api } = instanceIdApi();
    installChrome({ instanceID: api });
    await rejectsWith(() => gcmDeleteToken({}), "invalid argument");
  });

  it("deletes the token and echoes the entity", async () => {
    const { calls, api } = instanceIdApi();
    installChrome({ instanceID: api });
    assert.deepEqual(await gcmDeleteToken({ entity: "123", scope: "GCM" }), { deleted: "123" });
    assert.deepEqual(calls[0], {
      fn: "deleteToken",
      arg: { authorizedEntity: "123", scope: "GCM" },
    });
  });
});

describe("listenGcm", () => {
  function gcmWith() {
    type Listener = (message: Any) => void;
    const listeners: Listener[] = [];
    const events: Event[] = [];
    installChrome({
      gcm: { onMessage: { addListener: (fn: Listener) => void listeners.push(fn) } },
    });
    setEventSink((event) => void events.push(event));
    return { events, fire: (message: Any) => listeners[0](message) };
  }

  it("does nothing when the browser has no gcm API", () => {
    installChrome({});
    assert.doesNotThrow(() => listenGcm());
  });

  it("forwards a push message as lg:gcm.message", () => {
    const stub = gcmWith();
    listenGcm();
    stub.fire({ from: "123", collapseKey: "k", data: { a: "1" } });
    assert.equal(stub.events[0].method, "lg:gcm.message");
    assert.deepEqual(stub.events[0].params, {
      from: "123",
      collapseKey: "k",
      data: { a: "1" },
    });
  });

  it("normalises a missing collapseKey to null and missing data to {}", () => {
    const stub = gcmWith();
    listenGcm();
    stub.fire({ from: "123" });
    assert.deepEqual(stub.events[0].params, { from: "123", collapseKey: null, data: {} });
  });
});
