import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  wauthAttach,
  wauthComplete,
  wauthDetach,
  listenWauth,
} from "../src/handlers/wauth.ts";
import { setEventSink } from "../src/events.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

afterEach(() => {
  clearChrome();
  setEventSink(null);
});

function wauthWorld(): { gets: Any[]; creates: Any[] } {
  const state = { gets: [] as Any[], creates: [] as Any[] };
  installChrome({
    webAuthenticationProxy: {
      attach: async () => {},
      detach: async () => {},
      completeGetRequest: async (id: string, r: Any) => void state.gets.push({ id, r }),
      completeCreateRequest: async (id: string, r: Any) => void state.creates.push({ id, r }),
    },
  });
  return state;
}

describe("wauth", () => {
  it("attaches and detaches the proxy", async () => {
    wauthWorld();
    assert.deepEqual(await wauthAttach(), { attached: true });
    assert.deepEqual(await wauthDetach(), { detached: true });
  });

  it("completes get and create requests", async () => {
    const s = wauthWorld();
    assert.deepEqual(
      await wauthComplete({ request: "r1", kind: "get", httpStatusCode: 200 }),
      { completed: "r1" },
    );
    assert.deepEqual(s.gets, [{ id: "r1", r: { httpStatusCode: 200 } }]);
    assert.deepEqual(
      await wauthComplete({ request: "r2", kind: "create", httpStatusCode: 401, headers: { a: "b" } }),
      { completed: "r2" },
    );
    assert.deepEqual(s.creates, [{ id: "r2", r: { httpStatusCode: 401, headers: { a: "b" } } }]);
  });

  it("validates its arguments", async () => {
    wauthWorld();
    await rejectsWith(() => wauthComplete({ kind: "get", httpStatusCode: 200 }), "invalid argument");
    await rejectsWith(() => wauthComplete({ request: "r", httpStatusCode: 200 }), "invalid argument");
    await rejectsWith(() => wauthComplete({ request: "r", kind: "nope", httpStatusCode: 200 }), "invalid argument");
    await rejectsWith(() => wauthComplete({ request: "r", kind: "get" }), "invalid argument");
    await rejectsWith(
      () => wauthComplete({ request: "r", kind: "get", httpStatusCode: 200, headers: "x" }),
      "invalid argument",
    );
  });

  it("refuses when the browser has no webAuthenticationProxy", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => wauthAttach(), "unsupported operation");
    await rejectsWith(() => wauthDetach(), "unsupported operation");
    await rejectsWith(() => wauthComplete({ request: "r", kind: "get", httpStatusCode: 200 }), "unsupported operation");
  });
});

describe("wauth.listenWauth", () => {
  it("forwards requests as events", () => {
    const events: Any[] = [];
    setEventSink((e) => events.push(e));
    let onRequest: ((e: Any) => void) | undefined;
    installChrome({
      webAuthenticationProxy: {
        onRequest: { addListener: (fn: (e: Any) => void) => (onRequest = fn) },
      },
    });
    listenWauth();
    onRequest?.({ requestId: "r1", type: "get" });
    assert.deepEqual(events[0], {
      type: "event",
      method: "lg:wauth.request",
      params: { request: "r1", kind: "get", raw: { requestId: "r1", type: "get" } },
    });
  });

  it("tolerates a missing API and listener", () => {
    installChrome({});
    assert.doesNotThrow(() => listenWauth());
    installChrome({ webAuthenticationProxy: {} });
    assert.doesNotThrow(() => listenWauth());
  });
});
