import { deepStrictEqual, strictEqual } from "node:assert/strict";
import { test } from "node:test";

import { manifestFor } from "../manifest.mjs";

const chromeManifest = {
  manifest_version: 3,
  name: "browse",
  key: "MIIBIjAN",
  minimum_chrome_version: "122",
  background: { service_worker: "background.js", type: "module" },
  host_permissions: ["file:///*"],
};

test("chrome 版原样返回", () => {
  strictEqual(manifestFor("chrome", chromeManifest), chromeManifest);
});

test("firefox 版把 service_worker 换成 scripts，并去掉 Chrome 专有键", () => {
  deepStrictEqual(manifestFor("firefox", chromeManifest), {
    manifest_version: 3,
    name: "browse",
    background: { scripts: ["background.js"], type: "module" },
    host_permissions: ["file:///*"],
  });
});

test("firefox 版：没有 type 就不补一个", () => {
  const out = manifestFor("firefox", { background: { service_worker: "sw.js" } });
  deepStrictEqual(out.background, { scripts: ["sw.js"] });
});

test("firefox 版：本来就是 scripts 形状的 background 原样保留", () => {
  const out = manifestFor("firefox", { name: "x", background: { scripts: ["a.js"] } });
  deepStrictEqual(out, { name: "x", background: { scripts: ["a.js"] } });
});

test("firefox 版：没有 background 的 manifest 不会凭空长出一个", () => {
  const out = manifestFor("firefox", { name: "x", key: "k" });
  deepStrictEqual(out, { name: "x" });
  strictEqual("background" in out, false);
});
