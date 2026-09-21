/**
 * 策略：存储、拒绝名单、高危动作判定。裁决那一半（模式 / 免确认名单）在
 * `confirm.test.ts` 里，因为它要连着 hook 一起测才说明问题。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import {
  CONFIG_KEY,
  CONFIRM_MODES,
  DEFAULTS,
  FEATURES,
  FEATURE_OF,
  decide,
  domainMatches,
  domainOf,
  enforceDenyList,
  enforceFeatureToggles,
  getConfig,
  readConfig,
  riskyAction,
  setConfig,
  targetUrl,
} from "../src/policy.ts";
import { clearChrome, rejectsWith, storageMock } from "./mock.ts";

afterEach(clearChrome);

// ------------------------------------------------------------------ 存储

test("没存过配置时给的是默认值", async () => {
  storageMock();
  assert.deepEqual(await getConfig(), DEFAULTS);
});

test("存下去再读回来", async () => {
  const store = storageMock();
  await setConfig({ confirm_mode: "always", deny_domains: ["bank.test"] });
  assert.equal((await getConfig()).confirm_mode, "always");
  assert.deepEqual((store.data[CONFIG_KEY] as { deny_domains: string[] }).deny_domains,
                   ["bank.test"]);
});

test("合并保存：没提到的字段原样留着", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, approved_domains: ["shop.test"] } });
  await setConfig({ audit: false });
  const got = await getConfig();
  assert.deepEqual(got.approved_domains, ["shop.test"]);
  assert.equal(got.audit, false);
});

test("非法值写不进去", async () => {
  storageMock();
  await rejectsWith(
    () => setConfig({ confirm_mode: "loud" as never }),
    "invalid argument",
  );
  await rejectsWith(
    () => setConfig({ audit_retention_days: 1.5 }),
    "invalid argument",
  );
  assert.deepEqual(await getConfig(), DEFAULTS, "校验没过就一个字都不该落盘");
});

test("空的 confirm_mode 被拒，不会被悄悄填成最松的 silent", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, confirm_mode: "always" } });
  await rejectsWith(() => setConfig({ confirm_mode: "" as never }), "invalid argument");
  assert.equal((await getConfig()).confirm_mode, "always", "原来的模式不该被动");
});

test("域名规范化：去空白、去前导点和星号、去重、转小写", async () => {
  storageMock();
  const got = await setConfig({ deny_domains: [" *.Bank.test ", ".bank.test", "shop.test", ""] });
  assert.deepEqual(got.deny_domains, ["bank.test", "shop.test"]);
});

test("存储里是垃圾也不炸，按默认值办", async () => {
  storageMock({ [CONFIG_KEY]: { confirm_mode: 42, deny_domains: "not a list" } });
  const got = await getConfig();
  assert.equal(got.confirm_mode, DEFAULTS.confirm_mode);
  assert.deepEqual(got.deny_domains, []);
});

test("读不出来时：裁决路径退到最严，设置页拿到的是异常", async () => {
  clearChrome();
  // 裁决路径：宁可多问几次，也不能因为读不到配置就静默放行
  assert.equal((await getConfig()).confirm_mode, "always");
  // 设置页：要看见错误，而不是一份看起来正常的默认值
  await assert.rejects(() => readConfig());
});

// ------------------------------------------------------------------ 域名

test("URL / 裸域名都取得出主机名", () => {
  assert.equal(domainOf("https://a.test/x?y=1"), "a.test");
  assert.equal(domainOf("a.test"), "a.test");
  assert.equal(domainOf(".a.test"), "a.test");
  assert.equal(domainOf("a.test:8080"), "a.test");
  assert.equal(domainOf("A.TEST"), "a.test");
  assert.equal(domainOf(null), null);
  assert.equal(domainOf(""), null);
});

test("域名匹配连子域一起盖，两种写法同义", () => {
  for (const pattern of ["example.com", "*.example.com", ".example.com"]) {
    assert.equal(domainMatches("example.com", pattern), true, pattern);
    assert.equal(domainMatches("a.example.com", pattern), true, pattern);
    assert.equal(domainMatches("notexample.com", pattern), false, pattern);
  }
  assert.equal(domainMatches(null, "example.com"), false);
  assert.equal(domainMatches("example.com", ""), false);
});

test("目标 URL 从 url 或 domain 里取，两个都没有就是浏览器全局", () => {
  assert.equal(targetUrl({ url: "https://a.test" }), "https://a.test");
  assert.equal(targetUrl({ domain: "a.test" }), "a.test");
  assert.equal(targetUrl({}), null);
  assert.equal(targetUrl(undefined), null);
});

// ------------------------------------------------------------------ 拒绝名单

test("命中拒绝名单就抛，子域也算", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, deny_domains: ["bank.test"] } });
  for (const url of ["https://bank.test/x", "https://a.bank.test/", "bank.test"]) {
    await rejectsWith(
      () => enforceDenyList("browsingContext.navigate", url),
      "lg:user rejected",
    );
  }
});

test("拒绝名单管全部方法，不只高危的那些", async () => {
  // 拉黑一个域名之后连导航过去都不该允许
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, deny_domains: ["bank.test"] } });
  await rejectsWith(
    () => enforceDenyList("browsingContext.navigate", "https://bank.test/"),
    "lg:user rejected",
  );
});

test("没命中就放行，名单空着更是直接放行", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, deny_domains: ["bank.test"] } });
  await enforceDenyList("browsingContext.navigate", "https://ok.test/");
  storageMock({ [CONFIG_KEY]: DEFAULTS });
  await enforceDenyList("browsingContext.navigate", "https://bank.test/");
});

// ------------------------------------------------------------------ 功能开关

test("功能目录盖住每个功能的方法，一个方法只属于一个功能", () => {
  const all = FEATURES.flatMap((feature) => feature.methods);
  assert.equal(new Set(all).size, all.length, "方法重复出现在两个功能里");
  assert.equal(FEATURE_OF.size, all.length);
  assert.equal(FEATURE_OF.get("browsingContext.navigate")?.id, "tabs");
  assert.equal(FEATURE_OF.get("lg:page.snapshot")?.id, "snapshot");
});

test("全局禁用：这个功能全拒，别的功能照放", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, disabled_features: ["script"] } });
  await rejectsWith(
    () => enforceFeatureToggles("script.evaluate", "https://a.test/"),
    "lg:feature disabled",
  );
  await rejectsWith(
    () => enforceFeatureToggles("script.callFunction", null),
    "lg:feature disabled",
  );
  await enforceFeatureToggles("browsingContext.navigate", "https://a.test/");
});

test("按域名禁用：子域一起算，别的域名照放", async () => {
  storageMock({
    [CONFIG_KEY]: { ...DEFAULTS, domain_disabled_features: { "a.test": ["storage"] } },
  });
  await rejectsWith(
    () => enforceFeatureToggles("storage.getCookies", "a.test"),
    "lg:feature disabled",
  );
  await rejectsWith(
    () => enforceFeatureToggles("storage.setCookie", "https://sub.a.test/x"),
    "lg:feature disabled",
  );
  await enforceFeatureToggles("storage.getCookies", "b.test");
  // 按域名禁用管不到不知道域名的指令 —— 和拒绝名单同一条边界
  await enforceFeatureToggles("storage.getCookies", null);
});

test("审计的出口没有开关，永远放行", async () => {
  storageMock({
    [CONFIG_KEY]: {
      ...DEFAULTS,
      disabled_features: FEATURES.map((feature) => feature.id),
      domain_disabled_features: { a: ["tabs"], b: ["tabs"] },
    },
  });
  await enforceFeatureToggles("lg:audit.read", null);
  await enforceFeatureToggles("lg:audit.clear", null);
});

test("不认识的功能 id 写不进去，存进来的垃圾被清掉", async () => {
  storageMock();
  await rejectsWith(
    () => setConfig({ disabled_features: ["tabs", "nope"] }),
    "invalid argument",
  );
  await rejectsWith(
    () => setConfig({ domain_disabled_features: { "a.test": ["nope"] } }),
    "invalid argument",
  );
  assert.deepEqual(await getConfig(), DEFAULTS, "校验没过就一个字都不该落盘");

  storageMock({
    [CONFIG_KEY]: {
      ...DEFAULTS,
      disabled_features: [42, "tabs", "tabs"],
      domain_disabled_features: { "*.A.test ": ["script"], "b.test": [] },
    },
  });
  const got = await getConfig();
  assert.deepEqual(got.disabled_features, ["tabs"]);
  assert.deepEqual(got.domain_disabled_features, { "a.test": ["script"] }, "空名单的键整个丢掉");
});

// ------------------------------------------------------------------ 高危判定

test("高危方法表逐条对得上", () => {
  assert.equal(riskyAction("storage.getCookies"), "readCookies");
  assert.equal(riskyAction("script.evaluate"), "evalMainWorld");
  assert.equal(riskyAction("lg:downloads.start"), "download");
  assert.equal(riskyAction("browsingContext.navigate"), null);
});

test("带 js= 定位器的 input.* 也算高危 —— 光看方法名会漏", () => {
  assert.equal(riskyAction("input.click", { selector: "js=document.body" }), "evalMainWorld");
  assert.equal(riskyAction("input.click", { selector: "css=button" }), null);
});

// ------------------------------------------------------------------ 模式

test("三个模式就这三个，顺序由松到紧", () => {
  assert.deepEqual(CONFIRM_MODES, ["silent", "per_domain", "always"]);
});

test("decide：silent 放行、always 问、per_domain 看名单", async () => {
  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, confirm_mode: "silent" } });
  assert.equal(await decide("https://a.test/"), "allow");

  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, confirm_mode: "always",
                                approved_domains: ["a.test"] } });
  assert.equal(await decide("https://a.test/"), "ask", "always 模式下同意过也照问");

  storageMock({ [CONFIG_KEY]: { ...DEFAULTS, confirm_mode: "per_domain",
                                approved_domains: ["a.test"] } });
  assert.equal(await decide("https://a.test/"), "allow");
  assert.equal(await decide("https://b.test/"), "ask");
  assert.equal(await decide(null), "ask", "不知道是哪个站就得问");
});
