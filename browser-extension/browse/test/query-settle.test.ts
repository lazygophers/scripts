import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { CommandError, querySettle } from "../src/protocol.ts";

const never = () => new Promise<never>(() => {});

describe("querySettle", () => {
  it("passes through a resolved value", async () => {
    assert.equal(await querySettle(Promise.resolve(7), "q", 50), 7);
  });

  it("passes through a rejection untouched", async () => {
    await assert.rejects(
      querySettle(Promise.reject(new Error("boom")), "q", 50),
      /boom/,
    );
  });

  it("turns a hanging query into a timeout CommandError", async () => {
    await assert.rejects(querySettle(never(), "chrome.tabs.query", 10), (err) => {
      assert.ok(err instanceof CommandError);
      assert.equal(err.code, "timeout");
      assert.match(err.message, /chrome\.tabs\.query/);
      assert.match(err.message, /Arc/);
      return true;
    });
  });
});
