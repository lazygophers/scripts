import assert from "node:assert/strict";
import test from "node:test";
import { wauthAttach, wauthComplete, wauthDetach, listenWauth } from "../src/handlers/wauth.ts";
import { installChrome, ownSession, rejectsWith } from "./mock.ts";
import { setEventSink } from "../src/events.ts";

type Any = Record<string, unknown>;

function setup(calls: Any = {}) {
  const requests: Any[] = [];
  const proxy = {
    attach: async () => {
      calls.attach = (calls.attach as number ?? 0) + 1;
    },
    detach: async () => {
      calls.detach = (calls.detach as number ?? 0) + 1;
    },
    completeGetRequest: async (id: string, r: Any) => {
      requests.push({ kind: "get", id, r });
    },
    completeCreateRequest: async (id: string, r: Any) => {
      requests.push({ kind: "create", id, r });
    },
    onRequest: { addListener: (fn: (e: Any) => void) => { calls.listener = fn; } },
  };
  installChrome({
    ...ownSession(),
    webAuthenticationProxy: proxy as Any,
  });
  return { calls, requests, proxy };
}

test("attach/detach round-trip the proxy", async () => {
  const { calls } = setup();
  assert.deepEqual(await wauthAttach(), { attached: true });
  assert.deepEqual(await wauthDetach(), { detached: true });
  assert.equal(calls.attach, 1);
  assert.equal(calls.detach, 1);
});

test("complete validates kind, status code and headers", async () => {
  const { requests } = setup();
  await rejectsWith(
    () => wauthComplete({ request: "r1", kind: "sign", httpStatusCode: 200 }),
    "invalid argument",
  );
  await rejectsWith(
    () => wauthComplete({ request: "r1", kind: "get", httpStatusCode: "200" }),
    "invalid argument",
  );
  await rejectsWith(
    () => wauthComplete({ request: "r1", kind: "get", httpStatusCode: 200, headers: "x" }),
    "invalid argument",
  );
  assert.deepEqual(requests, []);

  assert.deepEqual(await wauthComplete({ request: "r1", kind: "get", httpStatusCode: 200 }), {
    completed: "r1",
  });
  assert.deepEqual(await wauthComplete({
    request: "r2",
    kind: "create",
    httpStatusCode: 201,
    headers: { "x-a": "1" },
  }), { completed: "r2" });
  assert.deepEqual(requests, [
    { kind: "get", id: "r1", r: { httpStatusCode: 200 } },
    { kind: "create", id: "r2", r: { httpStatusCode: 201, headers: { "x-a": "1" } } },
  ]);
});

test("listenWauth forwards onRequest into lg:wauth.request events", async () => {
  const { calls } = setup();
  const seen: Any[] = [];
  setEventSink((event) => seen.push(event));
  listenWauth();
  (calls.listener as (e: Any) => void)({ requestId: "r9", type: "create" });
  setEventSink(null);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, "lg:wauth.request");
  const params = seen[0].params as Any;
  assert.equal(params.request, "r9");
  assert.equal(params.kind, "create");
  assert.equal((params.raw as Any).requestId, "r9");
});
