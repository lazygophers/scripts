import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { profileGet, profileSet } from "../src/handlers/profile.ts";
import { clearChrome, rejectsWith, storageMock } from "./mock.ts";

/** profile 名存在 chrome.storage.local，每个 profile 一份 —— 桩 storage 验证读写。 */
afterEach(clearChrome);

describe("profileGet / profileSet", () => {
  it("reports an empty profile before anything is set", async () => {
    storageMock();
    assert.deepEqual(await profileGet(), { profile: "" });
  });

  it("persists the name into storage and reports it back", async () => {
    const store = storageMock();
    assert.deepEqual(await profileSet({ name: "工作" }), { profile: "工作" });
    assert.equal(store.data["browse:profileName"], "工作");
    assert.deepEqual(await profileGet(), { profile: "工作" });
  });

  it("an empty string clears the profile", async () => {
    const store = storageMock();
    await profileSet({ name: "工作" });
    assert.deepEqual(await profileSet({ name: "" }), { profile: "" });
    assert.equal(store.data["browse:profileName"], "");
  });

  it("refuses a missing name and an over-long one", async () => {
    storageMock();
    await rejectsWith(() => profileSet({}), "invalid argument");
    await rejectsWith(() => profileSet({ name: "x".repeat(65) }), "invalid argument");
  });
});
