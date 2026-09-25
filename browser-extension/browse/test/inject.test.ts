import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import type { Target } from "../src/handlers/context.ts";
import { execInPage, runInPage } from "../src/handlers/inject.ts";
import type { PageResult } from "../src/handlers/inject.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

const TAB: Target = { tabId: 7, frameId: undefined };
const FRAME: Target = { tabId: 7, frameId: 3 };

/**
 * `chrome.scripting` 的桩：把每次注入记下来，`files:` 那次回 undefined
 * （注入文件本来就没有返回值），带 `func` 的那次回 `results` 里排队的下一个。
 */
function scriptingWith(...results: unknown[]) {
  const injections: Any[] = [];
  const queue = [...results];
  installChrome({
    scripting: {
      executeScript: async (injection: Any) => {
        injections.push(injection);
        if (Array.isArray(injection.files)) {
          return [{ result: undefined }];
        }
        return [{ result: queue.shift() }];
      },
    },
  });
  return injections;
}

const ok = <T,>(value: T): PageResult<T> => ({ ok: true, value });

afterEach(clearChrome);

describe("execInPage", () => {
  it("targets the whole tab when frameId is undefined", async () => {
    const injections = scriptingWith(ok(1));
    await execInPage(TAB, "ISOLATED", () => ok(1), []);
    assert.deepEqual(injections[0].target, { tabId: 7 });
  });

  it("targets one frame when frameId is given", async () => {
    const injections = scriptingWith(ok(1));
    await execInPage(FRAME, "ISOLATED", () => ok(1), []);
    assert.deepEqual(injections[0].target, { tabId: 7, frameIds: [3] });
  });

  it("frame 0 is still an explicit frame id, not the bare tab", async () => {
    const injections = scriptingWith(ok(1));
    await execInPage({ tabId: 7, frameId: 0 }, "ISOLATED", () => ok(1), []);
    assert.deepEqual(injections[0].target, { tabId: 7, frameIds: [0] });
  });

  it("passes world and args through untouched", async () => {
    const injections = scriptingWith(ok("done"));
    await execInPage(TAB, "MAIN", (a: number, b: string) => ok(`${a}${b}`), [1, "x"]);
    assert.equal(injections[0].world, "MAIN");
    assert.deepEqual(injections[0].args, [1, "x"]);
  });

  it("does not inject the locator file — that is runInPage's job", async () => {
    const injections = scriptingWith(ok(1));
    await execInPage(TAB, "ISOLATED", () => ok(1), []);
    assert.equal(injections.length, 1);
    assert.equal(injections[0].files, undefined);
  });

  it("hands back a failed PageResult instead of throwing", async () => {
    scriptingWith({ ok: false, message: "no such element: #a" });
    assert.deepEqual(await execInPage(TAB, "ISOLATED", () => ok(1), []), {
      ok: false,
      message: "no such element: #a",
    });
  });

  it("turns a missing result into unknown error", async () => {
    // 页面在注入返回前被导航走，Chrome 会给一个没有 result 的条目
    scriptingWith(undefined);
    await rejectsWith(() => execInPage(TAB, "ISOLATED", () => ok(1), []), "unknown error");
  });
});

describe("runInPage", () => {
  it("injects the locator file before running the function", async () => {
    const injections = scriptingWith(ok("值"));
    assert.equal(await runInPage(TAB, "ISOLATED", () => ok("值"), []), "值");
    assert.equal(injections.length, 2);
    assert.deepEqual(injections[0].files, ["page-locate.js"]);
    assert.equal(injections[1].func !== undefined, true);
  });

  it("injects the locator into the same world as the function", async () => {
    const injections = scriptingWith(ok(1));
    await runInPage(TAB, "MAIN", () => ok(1), []);
    assert.equal(injections[0].world, "MAIN", "files: 也认 world，两次必须一致");
    assert.equal(injections[1].world, "MAIN");
  });

  it("unwraps the value on success", async () => {
    scriptingWith(ok({ nested: true }));
    assert.deepEqual(await runInPage(TAB, "ISOLATED", () => ok({ nested: true }), []), {
      nested: true,
    });
  });

  it("recovers the BiDi code from the page-side message", async () => {
    scriptingWith({ ok: false, message: "no such element: #missing" });
    const err = await rejectsWith(
      () => runInPage(TAB, "ISOLATED", () => ok(1), []),
      "no such element",
    );
    assert.match(err.message, /#missing/);
  });

  it("recovers invalid argument the same way", async () => {
    scriptingWith({ ok: false, message: "invalid argument: bad locator" });
    await rejectsWith(() => runInPage(TAB, "ISOLATED", () => ok(1), []), "invalid argument");
  });

  it("falls back to unknown error for a message with no known code", async () => {
    scriptingWith({ ok: false, message: "页面自己炸了" });
    await rejectsWith(() => runInPage(TAB, "ISOLATED", () => ok(1), []), "unknown error");
  });
});
