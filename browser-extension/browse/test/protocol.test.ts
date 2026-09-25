import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  CommandError,
  asString,
  isCommand,
  optionalString,
  requireString,
} from "../src/protocol.ts";

describe("CommandError", () => {
  it("carries the BiDi error code alongside the message", () => {
    const error = new CommandError("no such element", "nothing matched css=.x");
    assert.equal(error.code, "no such element");
    assert.equal(error.name, "CommandError");
    assert.equal(error.message, "nothing matched css=.x");
    assert.ok(error instanceof Error);
  });
});

describe("asString", () => {
  it("passes strings through, empty included", () => {
    assert.equal(asString("value", "text"), "value");
    assert.equal(asString("", "text"), "");
  });

  it("rejects every non-string with an invalid argument code", () => {
    for (const bad of [undefined, null, 1, true, {}, []]) {
      assert.throws(
        () => asString(bad, "text"),
        (error: CommandError) =>
          error.code === "invalid argument" && error.message === "text must be a string",
      );
    }
  });

  it("appends the hint so the message says what to pass", () => {
    assert.throws(
      () => asString(1, "key", `, e.g. "Enter"`),
      (error: CommandError) => error.message === `key must be a string, e.g. "Enter"`,
    );
  });
});

describe("requireString", () => {
  it("accepts a non-empty string", () => {
    assert.equal(requireString("https://example.com", "url"), "https://example.com");
  });

  it("rejects the empty string as well as non-strings", () => {
    for (const bad of ["", undefined, null, 0, {}]) {
      assert.throws(
        () => requireString(bad, "url"),
        (error: CommandError) =>
          error.code === "invalid argument" && error.message === "url must be a non-empty string",
      );
    }
  });
});

describe("optionalString", () => {
  it("lets undefined through untouched", () => {
    assert.equal(optionalString(undefined, "title"), undefined);
  });

  it("still validates anything that is present, null included", () => {
    assert.equal(optionalString("", "title"), "");
    assert.throws(() => optionalString(null, "title"), (e: CommandError) => e.code === "invalid argument");
    assert.throws(() => optionalString(7, "title"), (e: CommandError) => e.code === "invalid argument");
  });
});

describe("isCommand", () => {
  it("accepts a numeric id plus a method, with or without params", () => {
    assert.equal(isCommand({ id: 1, method: "browsingContext.navigate" }), true);
    assert.equal(isCommand({ id: 0, method: "lg:history.search", params: {} }), true);
  });

  it("rejects anything missing the two required fields", () => {
    for (const bad of [
      null,
      undefined,
      "command",
      42,
      {},
      { id: 1 },
      { method: "x" },
      { id: "1", method: "x" },
      { id: 1, method: 2 },
    ]) {
      assert.equal(isCommand(bad), false, `should reject ${JSON.stringify(bad)}`);
    }
  });
});
