import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { NativeConnection } from "../src/native-port.ts";
import { clearChrome, installChrome } from "./mock.ts";

type Any = Record<string, unknown>;

/** Every connection a test opened. The 20s keepalive interval and the
 * reconnect timer both keep node alive, so each one must be shut down. */
const open: NativeConnection[] = [];

/**
 * A `connectNative` port under test control: whatever the extension posts
 * lands in `sent`, and `reply` plays the daemon's answer back.
 */
function port(): {
  connection: NativeConnection;
  sent: Any[];
  reply: (message: Any) => void;
  drop: () => void;
} {
  const sent: Any[] = [];
  let onMessage: (msg: unknown) => void = () => {};
  let onDisconnect: () => void = () => {};
  installChrome({
    runtime: {
      connectNative: () => ({
        postMessage: (msg: Any) => sent.push(msg),
        disconnect: () => {},
        onMessage: { addListener: (fn: (msg: unknown) => void) => (onMessage = fn) },
        onDisconnect: { addListener: (fn: () => void) => (onDisconnect = fn) },
      }),
      lastError: undefined,
    },
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
      setTitle: async () => {},
    },
  });
  const connection = new NativeConnection(
    () => {},
    () => {},
  );
  open.push(connection);
  connection.connect();
  return {
    connection,
    sent,
    reply: (message) => onMessage(message),
    drop: () => onDisconnect(),
  };
}

afterEach(() => {
  while (open.length) {
    open.pop()?.disconnect();
  }
  clearChrome();
});

test("request sends a Command and resolves with the daemon's result", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.list");
  assert.deepEqual(p.sent.at(-1), {
    id: 1,
    method: "lg:approvals.list",
    params: {},
  });
  p.reply({ type: "success", id: 1, result: { domains: ["bank.test"] } });
  assert.deepEqual(await answer, { domains: ["bank.test"] });
});

test("two requests in flight come back to the right caller", async () => {
  const p = port();
  const first = p.connection.request("lg:approvals.approve", { domain: "a.test" });
  const second = p.connection.request("lg:approvals.revoke", { domain: "b.test" });
  // Answered out of order on purpose: matching is by id, not arrival.
  p.reply({ type: "success", id: 2, result: { domains: ["a.test"] } });
  p.reply({ type: "success", id: 1, result: { domains: ["a.test", "b.test"] } });
  assert.deepEqual(await first, { domains: ["a.test", "b.test"] });
  assert.deepEqual(await second, { domains: ["a.test"] });
});

test("an error reply rejects with the daemon's code", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.approve", {});
  p.reply({ type: "error", id: 1, error: "invalid argument", message: "要给一个 domain" });
  await assert.rejects(answer, (err: Error & { code?: string }) => {
    assert.equal(err.code, "invalid argument");
    assert.equal(err.message, "要给一个 domain");
    return true;
  });
});

test("a dropped port rejects everything in flight instead of hanging the panel", async () => {
  const p = port();
  const answer = p.connection.request("lg:approvals.list");
  p.drop();
  await assert.rejects(answer, (err: Error & { code?: string }) => {
    assert.equal(err.code, "lg:browser not connected");
    return true;
  });
});

test("requesting with no port at all rejects rather than silently dropping", async () => {
  installChrome({
    runtime: {
      connectNative: () => {
        throw new Error("no host manifest");
      },
    },
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
      setTitle: async () => {},
    },
  });
  const connection = new NativeConnection(
    () => {},
    () => {},
  );
  open.push(connection);
  connection.connect();
  await assert.rejects(connection.request("lg:approvals.list"), /not connected/);
});

test("a daemon Command is still dispatched, not mistaken for a reply", async () => {
  const p = port();
  p.reply({ id: 7, method: "lg:nonsense.do", params: {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const out = p.sent.at(-1) as Any;
  assert.equal(out.type, "error");
  assert.equal(out.id, 7);
  assert.equal(out.error, "unsupported operation");
});
