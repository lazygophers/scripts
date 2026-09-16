import assert from "node:assert/strict";
import test from "node:test";
import { read } from "../src/audit.ts";
import { confirm, setConfirmHook } from "../src/handlers/confirm.ts";
import { HANDLERS, dispatch } from "../src/handlers/index.ts";
import { FEATURE_OF } from "../src/policy.ts";
import { clearChrome, rejectsWith, storageMock } from "./mock.ts";

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
  "lg:tabs.group",
  "lg:tabs.ungroup",
  "lg:tabs.groups",
  "lg:tabs.updateGroup",
  // Spec 6.4's discovery command, listed alongside the locator schemes rather
  // than in the 5.1 block.
  "lg:page.snapshot",
  // 2026-09-16 扩容面（spec 外追加，扩展端 handlers/index.ts 逐条对齐）
  "lg:downloads.open",
  "lg:pageCapture.saveMhtml",
  "lg:capture.recordTab",
  "lg:capture.recordStop",
  "lg:capture.recordDesktop",
  "lg:offscreen.documents",
  "lg:clipboard.read",
  "lg:clipboard.write",
  "lg:readingList.list",
  "lg:readingList.add",
  "lg:readingList.update",
  "lg:readingList.remove",
  "lg:topSites.list",
  "lg:search.query",
  "lg:dns.resolve",
  "lg:idle.state",
  "lg:processes.list",
  "lg:system.info",
  "lg:notifications.show",
  "lg:notifications.clear",
  "lg:power.keepAwake",
  "lg:power.release",
  "lg:proxy.get",
  "lg:proxy.set",
  "lg:proxy.clear",
  "lg:permissions.getAll",
  "lg:permissions.contains",
  "lg:permissions.request",
  "lg:permissions.remove",
  "lg:gcm.id",
  "lg:gcm.token",
  "lg:gcm.deleteToken",
  "lg:userScripts.register",
  "lg:userScripts.list",
  "lg:userScripts.unregister",
  "lg:userScripts.reset",
  "lg:userScripts.world",
  "lg:declContent.setRules",
  "lg:declContent.clear",
  "lg:commands.list",
  "lg:sidePanel.open",
  "lg:sidePanel.close",
  "lg:sidePanel.behavior",
  "lg:omnibox.setDefault",
  "lg:wauth.attach",
  "lg:wauth.detach",
  "lg:wauth.complete",
  "lg:printing.printers",
  "lg:printing.jobs",
  "lg:printing.submit",
  "lg:printing.cancelJob",
  "lg:printing.metrics",
  "lg:printing.respond",
];

/**
 * 不是能力，是审计的出口（spec 4.6）。日志存在 chrome.storage.local 里，命令行读不到
 * 那个存储，这两条就是 `browse audit` 唯一的取数路径。
 *
 * 2026-09-14 之前这里是 `lg:confirm.request` 和 `lg:context.url` —— daemon 反过来问
 * 插件的两条。裁决搬进插件之后 daemon 不再问任何问题，两条都删了。
 */
const AUDIT = ["lg:audit.read", "lg:audit.clear"];

test("the command table is exactly the v1 capability list plus the audit exits", () => {
  assert.deepEqual(Object.keys(HANDLERS).sort(), [...V1, ...AUDIT].sort());
});

test("the daemon's old reverse-RPC methods are gone for good", () => {
  // 留着的话就等于 daemon 还能问插件问题，而策略已经不在它那儿了
  for (const method of ["lg:confirm.request", "lg:context.url"]) {
    assert.equal(method in HANDLERS, false, `${method} 不该还在命令表里`);
  }
});

test("the feature catalogue covers every handler except the audit exits", () => {
  for (const method of Object.keys(HANDLERS)) {
    if (AUDIT.includes(method)) {
      continue;
    }
    assert.ok(FEATURE_OF.has(method), `${method} 不在任何功能里，设置页关不掉它`);
  }
  for (const method of FEATURE_OF.keys()) {
    assert.ok(method in HANDLERS, `${method} 在功能目录里但命令表没有它`);
  }
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
    "lg:user rejected",
  );
  assert.match(err.message, /user denied readCookies for storage.getCookies on a.test/);
  setConfirmHook(async () => true);
});

// ------------------------------------------------- 闸门和记账都在 dispatch 上

test("拒绝名单在 dispatch 上拦住，handler 一步都不走", async () => {
  storageMock({ "browse:config": { deny_domains: ["bank.test"] } });
  // 故意不装 chrome.tabs：真走到 handler 就会是另一个错误码，那就说明没拦住
  await rejectsWith(
    () => dispatch("browsingContext.navigate", { url: "https://bank.test/x" }),
    "lg:user rejected",
  );
  clearChrome();
});

test("被禁的功能在 dispatch 上拦住，handler 一步都不走", async () => {
  storageMock({ "browse:config": { disabled_features: ["storage"] } });
  // 故意不装 chrome.cookies：真走到 handler 就会是另一个错误码，那就说明没拦住
  const err = await rejectsWith(
    () => dispatch("storage.getCookies", { domain: "a.test" }),
    "lg:feature disabled",
  );
  assert.match(err.message, /storage/);
  const [entry] = await read();
  assert.equal(entry?.result, "denied");
  assert.match(entry?.error ?? "", /禁用/);
  clearChrome();
});

test("被拒的指令记成 denied，带上域名和原因", async () => {
  storageMock({ "browse:config": { deny_domains: ["bank.test"] } });
  await rejectsWith(
    () => dispatch("storage.getCookies", { domain: "bank.test" }),
    "lg:user rejected",
  );
  const [entry] = await read();
  assert.equal(entry?.result, "denied");
  assert.equal(entry?.domain, "bank.test");
  assert.equal(entry?.action, "readCookies");
  assert.match(entry?.error ?? "", /拒绝名单/);
  clearChrome();
});

test("失败的指令记成 error，成功的记成 success", async () => {
  storageMock();
  // 没装 chrome.tabs，handler 会炸；炸出来的东西必须被记成一条 error
  await assert.rejects(() => dispatch("browsingContext.navigate", { url: "https://a.test/" }));
  const entries = await read();
  assert.equal(entries.at(-1)?.result, "error");
  assert.equal(entries.at(-1)?.domain, "a.test");
  clearChrome();
});

test("读审计这件事本身不记审计 —— 否则越读越长", async () => {
  storageMock();
  await dispatch("lg:audit.read", {});
  await dispatch("lg:audit.read", {});
  assert.deepEqual(await read(), []);
  clearChrome();
});

test("审计写不进去也不能把指令搞挂", async () => {
  const store = storageMock();
  store.failNext = 999;
  // 这条指令该失败的原因是「没有 chrome.tabs」，不该变成「审计写不进去」
  const err = await dispatch("browsingContext.navigate", { url: "https://a.test/" })
    .then(() => null, (e: Error) => e);
  assert.ok(err instanceof Error);
  assert.doesNotMatch(err.message, /QUOTA/, "失败原因不该是审计");
  clearChrome();
});
