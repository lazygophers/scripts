import assert from "node:assert/strict";
import test from "node:test";
import { setEventSink } from "../src/events.ts";
import {
  networkReset,
  networkSubscribe,
  networkUnsubscribe,
} from "../src/handlers/network.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";
import type { Event } from "../src/protocol.ts";

type Any = Record<string, unknown>;
type Listener = (details: Any) => void;

function setup(): { fire: Record<string, Listener[]>; events: Event[] } {
  const fire: Record<string, Listener[]> = {
    onBeforeRequest: [],
    onSendHeaders: [],
    onCompleted: [],
    onErrorOccurred: [],
  };
  const hook = (name: string): Any => ({
    addListener: (fn: Listener) => fire[name]?.push(fn),
    removeListener: (fn: Listener) => {
      const list = fire[name] ?? [];
      list.splice(list.indexOf(fn), 1);
    },
  });
  installChrome({
    webRequest: {
      onBeforeRequest: hook("onBeforeRequest"),
      onSendHeaders: hook("onSendHeaders"),
      onCompleted: hook("onCompleted"),
      onErrorOccurred: hook("onErrorOccurred"),
    },
  });
  const events: Event[] = [];
  setEventSink((event) => events.push(event));
  return { fire, events };
}

function teardown(): void {
  networkReset();
  setEventSink(null);
  clearChrome();
}

function emit(fire: Record<string, Listener[]>, name: string, details: Any): void {
  for (const fn of fire[name] ?? []) fn(details);
}

test("a completed request becomes one metadata event, with no body field", async () => {
  const { fire, events } = setup();
  const { subscription } = await networkSubscribe({ matchUrl: "*/api/*" });

  emit(fire, "onBeforeRequest", {
    requestId: "1",
    url: "https://a.test/api/orders",
    method: "GET",
    type: "xmlhttprequest",
    tabId: 7,
    timeStamp: 1000,
  });
  emit(fire, "onSendHeaders", {
    requestId: "1",
    url: "https://a.test/api/orders",
    type: "xmlhttprequest",
    requestHeaders: [{ name: "Accept", value: "application/json" }],
  });
  emit(fire, "onCompleted", {
    requestId: "1",
    url: "https://a.test/api/orders",
    method: "GET",
    type: "xmlhttprequest",
    tabId: 7,
    timeStamp: 1120,
    statusCode: 200,
    fromCache: false,
    responseHeaders: [{ name: "Content-Type", value: "application/json" }],
  });

  assert.equal(events.length, 1);
  const event = events[0]!;
  assert.equal(event.method, "network.responseCompleted");
  assert.deepEqual(event.params.subscriptions, [subscription]);
  assert.deepEqual(event.params.response, {
    status: 200,
    fromCache: false,
    headers: { "Content-Type": "application/json" },
  });
  assert.equal(event.params["lg:durationMs"], 120);
  assert.equal(event.params["lg:metadataOnly"], true);
  const request = event.params.request as Any;
  assert.deepEqual(request.headers, { Accept: "application/json" });
  assert.equal(request.context, "7");
  assert.ok(!("body" in request));
  teardown();
});

test("a request outside the glob produces nothing", async () => {
  const { fire, events } = setup();
  await networkSubscribe({ matchUrl: "*/api/*" });
  emit(fire, "onCompleted", {
    requestId: "2",
    url: "https://a.test/static/app.js",
    method: "GET",
    type: "script",
    tabId: 7,
    timeStamp: 1,
    statusCode: 200,
    fromCache: false,
    responseHeaders: [],
  });
  assert.equal(events.length, 0);
  teardown();
});

test("the types filter narrows further", async () => {
  const { fire, events } = setup();
  await networkSubscribe({ types: ["xmlhttprequest"] });
  const base = { url: "https://a.test/x", method: "GET", tabId: -1, timeStamp: 1, statusCode: 200, fromCache: false, responseHeaders: [] };
  emit(fire, "onCompleted", { ...base, requestId: "3", type: "image" });
  assert.equal(events.length, 0);
  emit(fire, "onCompleted", { ...base, requestId: "4", type: "xmlhttprequest" });
  assert.equal(events.length, 1);
  assert.equal((events[0]!.params.request as Any).context, null);
  teardown();
});

test("a failed request emits fetchError with the browser's error text", async () => {
  const { fire, events } = setup();
  await networkSubscribe({});
  emit(fire, "onErrorOccurred", {
    requestId: "5",
    url: "https://a.test/gone",
    method: "GET",
    type: "xmlhttprequest",
    tabId: 7,
    timeStamp: 2,
    error: "net::ERR_NAME_NOT_RESOLVED",
  });
  assert.equal(events[0]?.method, "network.fetchError");
  assert.equal(events[0]?.params.errorText, "net::ERR_NAME_NOT_RESOLVED");
  teardown();
});

test("two subscriptions both get tagged on one event", async () => {
  const { fire, events } = setup();
  const a = await networkSubscribe({ matchUrl: "*/api/*" });
  const b = await networkSubscribe({});
  emit(fire, "onCompleted", {
    requestId: "6",
    url: "https://a.test/api/x",
    method: "GET",
    type: "xmlhttprequest",
    tabId: 7,
    timeStamp: 1,
    statusCode: 204,
    fromCache: false,
    responseHeaders: [],
  });
  assert.deepEqual(events[0]?.params.subscriptions, [a.subscription, b.subscription]);
  teardown();
});

test("unsubscribe detaches the listeners once the last one goes", async () => {
  const { fire, events } = setup();
  const { subscription } = await networkSubscribe({});
  assert.equal(fire.onCompleted?.length, 1);

  assert.deepEqual(await networkUnsubscribe({ subscription }), {
    removed: [subscription],
  });
  assert.equal(fire.onCompleted?.length, 0);

  emit(fire, "onCompleted", { requestId: "7", url: "x", type: "script", tabId: -1 });
  assert.equal(events.length, 0);
  teardown();
});

test("unsubscribing an unknown id is an error, not a silent no-op", async () => {
  setup();
  await rejectsWith(() => networkUnsubscribe({ subscription: "net-999" }), "invalid argument");
  teardown();
});

test("subscribe is refused when the browser has no webRequest", async () => {
  installChrome({});
  await rejectsWith(() => networkSubscribe({}), "unsupported operation");
  clearChrome();
});
