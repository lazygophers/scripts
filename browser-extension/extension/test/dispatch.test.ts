import assert from "node:assert/strict";
import test from "node:test";
import { confirm, setConfirmHook } from "../src/handlers/confirm.ts";
import { HANDLERS, dispatch } from "../src/handlers/index.ts";
import { rejectsWith } from "./mock.ts";

/** Spec 5.1, verbatim. If this list and HANDLERS disagree, one of them is wrong. */
const V1 = [
  "browsingContext.getTree",
  "browsingContext.create",
  "browsingContext.close",
  "browsingContext.activate",
  "browsingContext.navigate",
  "browsingContext.reload",
  "browsingContext.captureScreenshot",
  "script.evaluate",
  "script.callFunction",
  "input.click",
  "input.type",
  "input.scroll",
  "input.key",
  "storage.getCookies",
  "storage.setCookie",
  "storage.deleteCookies",
  "storage.getLocalStorage",
  "storage.setLocalStorage",
  "network.subscribe",
  "network.unsubscribe",
  "lg:history.search",
  "lg:history.delete",
  "lg:bookmarks.search",
  "lg:bookmarks.create",
  "lg:bookmarks.remove",
  "lg:downloads.start",
  "lg:downloads.list",
  "lg:downloads.cancel",
];

test("the command table is exactly the v1 capability list", () => {
  assert.deepEqual(Object.keys(HANDLERS).sort(), [...V1].sort());
});

test("a command outside v1 is unsupported operation, never a guess", async () => {
  // A real BiDi command this extension deliberately does not implement.
  await rejectsWith(() => dispatch("browsingContext.print", {}), "unsupported operation");
  await rejectsWith(() => dispatch("nonsense", {}), "unknown command");
});

test("the default confirm hook allows, which is confirm_mode: silent", async () => {
  await confirm({ action: "download", method: "lg:downloads.start", url: "https://a.test/" });
});

test("a hook that says no turns into a refusal naming the action", async () => {
  setConfirmHook(async () => false);
  const err = await rejectsWith(
    () => confirm({ action: "readCookies", method: "storage.getCookies", url: "a.test" }),
    "unknown error",
  );
  assert.match(err.message, /user denied readCookies for storage.getCookies on a.test/);
  setConfirmHook(async () => true);
});
