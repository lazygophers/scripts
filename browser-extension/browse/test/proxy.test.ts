import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { setConfirmHook } from "../src/handlers/confirm.ts";
import { proxyClear, proxyGet, proxySet } from "../src/handlers/proxy.ts";
import { CONFIG_KEY, DEFAULTS } from "../src/policy.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

type Any = Record<string, unknown>;

function chromeWith(extra: Any, config: Any = DEFAULTS): void {
  installChrome({ ...extra });
  storageMock({ [CONFIG_KEY]: config });
  setConfirmHook(async () => true);
}

/** proxy 桩：settings.set/clear 记参数，get 回 Chrome 真实的那两个字段。 */
function proxyApi() {
  const calls: { fn: string; arg: unknown }[] = [];
  return {
    calls,
    api: {
      settings: {
        set: async (arg: unknown) => void calls.push({ fn: "set", arg }),
        clear: async (arg: unknown) => void calls.push({ fn: "clear", arg }),
        get: async () => ({ value: { mode: "system" }, levelOfControl: "controlled_by_this_extension" }),
      },
    },
  };
}

afterEach(() => {
  clearChrome();
  setConfirmHook(async () => true);
});

describe("proxySet", () => {
  it("refuses when the browser has no proxy API", async () => {
    chromeWith({});
    await rejectsWith(() => proxySet({ mode: "direct" }), "unsupported operation");
  });

  it("needs a mode", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(() => proxySet({}), "invalid argument");
  });

  it("rejects a mode outside the four Chrome accepts", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(() => proxySet({ mode: "socks" }), "invalid argument");
  });

  it("sets a bare mode with regular scope", async () => {
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    assert.deepEqual(await proxySet({ mode: "direct" }), { mode: "direct" });
    assert.deepEqual(calls[0], { fn: "set", arg: { value: { mode: "direct" }, scope: "regular" } });
  });

  it("builds singleProxy rules for fixed_servers", async () => {
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    await proxySet({ mode: "fixed_servers", host: "127.0.0.1", port: 8080 });
    assert.deepEqual((calls[0].arg as Any).value, {
      mode: "fixed_servers",
      rules: { singleProxy: { scheme: "http", host: "127.0.0.1", port: 8080 } },
    });
  });

  it("forwards an explicit scheme so a SOCKS5 proxy stays SOCKS5", async () => {
    // 出处：https://developer.chrome.com/docs/extensions/reference/api/proxy
    // ProxyServer.scheme 可配，文档示例就是 scheme: "socks5"；不给才默认 http
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    await proxySet({ mode: "fixed_servers", host: "127.0.0.1", port: 1080, scheme: "socks5" });
    assert.deepEqual((calls[0].arg as Any).value, {
      mode: "fixed_servers",
      rules: { singleProxy: { scheme: "socks5", host: "127.0.0.1", port: 1080 } },
    });
  });

  it("rejects a scheme Chrome does not accept", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(
      () => proxySet({ mode: "fixed_servers", host: "127.0.0.1", port: 1080, scheme: "socks6" }),
      "invalid argument",
    );
  });

  it("rejects a non-string scheme", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(
      () => proxySet({ mode: "fixed_servers", host: "127.0.0.1", port: 1080, scheme: 5 }),
      "invalid argument",
    );
  });

  it("needs a host for fixed_servers", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(() => proxySet({ mode: "fixed_servers", port: 8080 }), "invalid argument");
  });

  it("rejects a port outside [1, 65535] or not an integer", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    for (const port of [0, 65536, 80.5, "8080"]) {
      await rejectsWith(
        () => proxySet({ mode: "fixed_servers", host: "127.0.0.1", port }),
        "invalid argument",
      );
    }
  });

  it("takes pacUrl for pac_script", async () => {
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    await proxySet({ mode: "pac_script", pacUrl: "https://e.test/p.pac" });
    assert.deepEqual((calls[0].arg as Any).value, {
      mode: "pac_script",
      pacScript: { url: "https://e.test/p.pac" },
    });
  });

  it("falls back to pacData when pacUrl is absent", async () => {
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    await proxySet({ mode: "pac_script", pacData: "function FindProxyForURL(){}" });
    assert.deepEqual((calls[0].arg as Any).value, {
      mode: "pac_script",
      pacScript: { data: "function FindProxyForURL(){}" },
    });
  });

  it("needs one of pacUrl / pacData for pac_script", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    await rejectsWith(() => proxySet({ mode: "pac_script" }), "invalid argument");
  });

  it("refuses when the user denies the browser-wide change", async () => {
    // 改的是整个浏览器的代理，所以拒绝必须发生在 settings.set 之前
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => false);
    await rejectsWith(() => proxySet({ mode: "direct" }), "lg:user rejected");
    assert.equal(calls.length, 0);
  });

  it("validates arguments before asking the user", async () => {
    let asked = false;
    const { api } = proxyApi();
    chromeWith({ proxy: api }, { ...DEFAULTS, confirm_mode: "always" });
    setConfirmHook(async () => {
      asked = true;
      return true;
    });
    await rejectsWith(() => proxySet({ mode: "socks" }), "invalid argument");
    assert.equal(asked, false, "参数错就不该打扰用户");
  });
});

describe("proxyGet", () => {
  it("refuses when the browser has no proxy API", async () => {
    chromeWith({});
    await rejectsWith(() => proxyGet(), "unsupported operation");
  });

  it("reports mode and who controls it", async () => {
    const { api } = proxyApi();
    chromeWith({ proxy: api });
    assert.deepEqual(await proxyGet(), {
      mode: "system",
      levelOfControl: "controlled_by_this_extension",
    });
  });
});

describe("proxyClear", () => {
  it("refuses when the browser has no proxy API", async () => {
    chromeWith({});
    await rejectsWith(() => proxyClear(), "unsupported operation");
  });

  it("hands control back on the regular scope", async () => {
    const { calls, api } = proxyApi();
    chromeWith({ proxy: api });
    assert.deepEqual(await proxyClear(), { cleared: true });
    assert.deepEqual(calls[0], { fn: "clear", arg: { scope: "regular" } });
  });
});
