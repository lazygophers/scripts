import assert from "node:assert/strict";
import test from "node:test";
import { setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import { scriptCallFunction, scriptEvaluate } from "../src/handlers/script.ts";
import { clearChrome, installChrome, rejectsWith, scriptingMock } from "./mock.ts";

type Any = Record<string, unknown>;

function setup(): { calls: Any[]; asked: ConfirmRequest[] } {
  const scripting = scriptingMock();
  installChrome({
    tabs: {
      query: async () => [{ id: 7, url: "https://a.test/", active: true }],
      get: async () => ({ id: 7, url: "https://a.test/" }),
    },
    scripting,
  });
  const asked: ConfirmRequest[] = [];
  setConfirmHook(async (request) => {
    asked.push(request);
    return true;
  });
  return { calls: scripting.calls, asked };
}

function teardown(): void {
  setConfirmHook(async () => true);
  clearChrome();
}

test("evaluate runs the expression in MAIN world and returns its value", async () => {
  const { calls } = setup();
  const result = await scriptEvaluate({ expression: "1 + 1" });
  assert.deepEqual(result, { type: "success", realm: "7", result: { value: 2 } });
  assert.equal(calls[0]?.world, "MAIN");
  // The expression crosses as a data argument, never as injected source.
  assert.deepEqual(calls[0]?.args, ["1 + 1", true]);
  teardown();
});

test("evaluate targets a frame when the context id names one", async () => {
  const { calls } = setup();
  const result = await scriptEvaluate({
    expression: "1",
    target: { context: "7.3" },
  });
  assert.equal(result.realm, "7.3");
  assert.deepEqual(calls[0]?.target, { tabId: 7, frameIds: [3] });
  teardown();
});

test("evaluate awaits a promise unless awaitPromise is false", async () => {
  setup();
  const awaited = await scriptEvaluate({ expression: "Promise.resolve(5)" });
  assert.equal(awaited.result.value, 5);
  const raw = await scriptEvaluate({
    expression: "Promise.resolve(5)",
    awaitPromise: false,
  });
  assert.ok(raw.result.value instanceof Promise);
  teardown();
});

test("a page exception comes back as an error, not as an empty value", async () => {
  setup();
  const err = await rejectsWith(
    () => scriptEvaluate({ expression: "(() => { throw new Error('boom') })()" }),
    "unknown error",
  );
  assert.match(err.message, /boom/);
  teardown();
});

test("MAIN world evaluation asks the confirm hook and a refusal stops it", async () => {
  const { asked } = setup();
  await scriptEvaluate({ expression: "1" });
  assert.deepEqual(asked[0], {
    action: "evalMainWorld",
    method: "script.evaluate",
    url: "https://a.test/",
  });

  setConfirmHook(async () => false);
  const err = await rejectsWith(() => scriptEvaluate({ expression: "1" }), "lg:user rejected");
  assert.match(err.message, /user denied evalMainWorld/);
  teardown();
});

test("callFunction applies JSON arguments and this", async () => {
  setup();
  const result = await scriptCallFunction({
    functionDeclaration: "function (a, b) { return this.k + a + b }",
    arguments: [2, 3],
    this: { k: 10 },
  });
  assert.equal(result.result.value, 15);
  teardown();
});

test("callFunction rejects a declaration that is not a function", async () => {
  setup();
  const err = await rejectsWith(
    () => scriptCallFunction({ functionDeclaration: "42" }),
    "unknown error",
  );
  assert.match(err.message, /did not evaluate to a function/);
  teardown();
});

test("callFunction rejects non-array arguments", async () => {
  setup();
  await rejectsWith(
    () => scriptCallFunction({ functionDeclaration: "() => 1", arguments: {} }),
    "invalid argument",
  );
  teardown();
});
