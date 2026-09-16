import assert from "node:assert/strict";
import test from "node:test";
import {
  BASE_DELAY_MS,
  COOLDOWN_AFTER_ATTEMPTS,
  COOLDOWN_MS,
  MAX_DELAY_MS,
  nextDelay,
} from "../src/backoff.ts";

test("first retry is never instant and never over the base window", () => {
  assert.equal(nextDelay(0, () => 0), BASE_DELAY_MS / 2);
  assert.equal(nextDelay(0, () => 1), BASE_DELAY_MS);
});

test("delay doubles per attempt until it caps at 60s", () => {
  assert.equal(nextDelay(1, () => 1), 1000);
  assert.equal(nextDelay(2, () => 1), 2000);
  assert.equal(nextDelay(9, () => 1), MAX_DELAY_MS);
});

test("jitter stays inside the upper half of the window", () => {
  for (let attempt = 0; attempt < COOLDOWN_AFTER_ATTEMPTS; attempt += 1) {
    const window = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
    for (let i = 0; i < 50; i += 1) {
      const d = nextDelay(attempt);
      assert.ok(d >= window / 2 && d <= window, `attempt ${attempt} gave ${d}`);
    }
  }
});

test("repeated failure switches to the long cooldown", () => {
  assert.equal(nextDelay(COOLDOWN_AFTER_ATTEMPTS), COOLDOWN_MS);
  assert.equal(nextDelay(100), COOLDOWN_MS);
});
