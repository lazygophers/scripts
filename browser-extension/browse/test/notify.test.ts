import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  notificationsClear,
  notificationsShow,
  powerKeepAwake,
  powerRelease,
} from "../src/handlers/notify.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

type Any = Record<string, unknown>;

afterEach(clearChrome);

describe("notifications", () => {
  it("refuses when the browser has no notifications API", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => notificationsShow({ title: "t", message: "m" }), "unsupported operation");
  });

  it("needs both a title and a message", async () => {
    installChrome({ notifications: { create: async () => "n1" }, runtime: {} });
    await rejectsWith(() => notificationsShow({ message: "m" }), "invalid argument");
    await rejectsWith(() => notificationsShow({ title: "t" }), "invalid argument");
    await rejectsWith(() => notificationsShow({ title: "", message: "m" }), "invalid argument");
  });

  it("falls back to the packaged icon", async () => {
    const created: Any[] = [];
    installChrome({
      notifications: { create: async (options: Any) => (created.push(options), "n1") },
      runtime: { getURL: (path: string) => `chrome-extension://id/${path}` },
    });
    const result = await notificationsShow({ title: "标题", message: "正文" });
    assert.deepEqual(result, { id: "n1" });
    assert.deepEqual(created[0], {
      type: "basic",
      iconUrl: "chrome-extension://id/assets/icon.png",
      title: "标题",
      message: "正文",
    });
  });

  it("uses a caller-supplied icon when given one", async () => {
    const created: Any[] = [];
    installChrome({
      notifications: { create: async (options: Any) => (created.push(options), "n2") },
      runtime: { getURL: (p: string) => p },
    });
    await notificationsShow({ title: "t", message: "m", iconUrl: "https://example.test/i.png" });
    assert.equal(created[0]?.iconUrl, "https://example.test/i.png");
  });

  it("clearing needs the id and reports whether anything was there", async () => {
    installChrome({ notifications: { clear: async (id: string) => id === "n1" }, runtime: {} });
    await rejectsWith(() => notificationsClear({}), "invalid argument");
    assert.deepEqual(await notificationsClear({ id: "n1" }), { cleared: true });
    assert.deepEqual(await notificationsClear({ id: "gone" }), { cleared: false });
  });
});

describe("power", () => {
  function powerChrome(): { requested: string[]; released: number } {
    const state = { requested: [] as string[], released: 0 };
    installChrome({
      runtime: {},
      power: {
        requestKeepAwake: (level: string) => void state.requested.push(level),
        releaseKeepAwake: () => void (state.released += 1),
      },
    });
    return state;
  }

  it("defaults to the system level", async () => {
    const state = powerChrome();
    assert.deepEqual(await powerKeepAwake({}), { level: "system" });
    assert.deepEqual(state.requested, ["system"]);
  });

  it("accepts display and rejects anything else", async () => {
    const state = powerChrome();
    assert.deepEqual(await powerKeepAwake({ level: "display" }), { level: "display" });
    await rejectsWith(() => powerKeepAwake({ level: "screen" }), "invalid argument");
    assert.deepEqual(state.requested, ["display"], "非法级别不该真的请求下去");
  });

  it("releases the request", async () => {
    const state = powerChrome();
    assert.deepEqual(await powerRelease(), { released: true });
    assert.equal(state.released, 1);
  });

  it("refuses when the browser has no power API", async () => {
    installChrome({ runtime: {} });
    await rejectsWith(() => powerKeepAwake({}), "unsupported operation");
    await rejectsWith(() => powerRelease(), "unsupported operation");
  });
});

