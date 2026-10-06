/**
 * 设置页。测的是「能做歪的地方」：
 *
 * - **daemon 没连上时照样能读能改能存** —— 用户提这整件事的起点就是这条
 * - 非法值有没有在扩展侧就被挡住
 * - `settings.html` 有没有被打包进 dist（漏了线上就是 404，本地开着 src/ 看不出来）
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

const SRC = join(import.meta.dirname, "..", "src");
const HTML = readFileSync(join(SRC, "settings.html"), "utf8");

type Any = Record<string, unknown>;

/**
 * 装一个 jsdom 的 realm 和一个假 chrome。**不带 `#form`**：settings.ts 的自举那一段
 * 就是靠 `#form` 在不在判断「我是不是真的跑在设置页上」，不带就只导入不副作用，
 * 测试可以自己摆好 DOM 再单独调它导出的函数。
 */
function realm(body: string, initial: Any = {}): { data: Any; dom: JSDOM } {
  const dom = new JSDOM(`<!doctype html><body>${body}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  const store: Any = { ...initial };
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string, args?: string[]) => `[${key}${args?.length ? ":" + args.join(",") : ""}]`,
    },
    // **没有 runtime.sendMessage**：设置页现在一句话都不跟 service worker 说，更不跟
    // daemon 说。真装了个 sendMessage 反而测不出「不依赖它」这件事。
    storage: {
      local: {
        get: async (key: string) => (key in store ? { [key]: store[key] } : {}),
        set: async (items: Any) => Object.assign(store, items),
        remove: async (key: string) => {
          delete store[key];
        },
      },
    },
  };
  return { data: store, dom };
}

afterEach(() => {
  const g = globalThis as Any;
  delete g.document;
  delete g.HTMLElement;
  delete g.chrome;
});

type SettingsModule = typeof import("../src/settings.ts");

let mod: SettingsModule | undefined;

/**
 * 模块只导入一次（带 query 破缓存会让 node 的覆盖率归因整段丢失——函数体全部
 * 报成未覆盖）。自举段因此导出成 `bootstrap()`：换 realm 的用例自己重跑它，
 * 等价于「新实例加载了一次」。
 */
async function load(): Promise<SettingsModule> {
  mod ??= await import("../src/settings.ts");
  return mod;
}

/** realm() 之后接自举：相当于页面在装好的 DOM 上加载。 */
async function boot(): Promise<SettingsModule> {
  const m = await load();
  m.bootstrap();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return m;
}

// ------------------------------------------------------------------ 校验

test("an unknown confirm_mode is refused in the extension, before it is sent", async () => {
  realm("");
  const { validate, CONFIRM_MODES } = await load();
  assert.deepEqual(CONFIRM_MODES, ["silent", "per_domain", "always"]);
  for (const mode of CONFIRM_MODES) {
    assert.equal(validate({ confirm_mode: mode }), "");
  }
  assert.equal(validate({ confirm_mode: "loud" }), "[settingsBadMode:loud]");
  assert.equal(validate({ confirm_mode: "" }), "[settingsBadMode:]");
});

test("keep-for must be a whole number", async () => {
  realm("");
  const { validate } = await load();
  assert.equal(validate({ audit_retention_days: 7 }), "");
  assert.equal(validate({ audit_retention_days: 0 }), "", "0 是合法的：永不删除");
  assert.equal(validate({ audit_retention_days: -1 }), "");
  assert.equal(validate({ audit_retention_days: Number.NaN }), "[settingsBadRetention]");
  assert.equal(validate({ audit_retention_days: 1.5 }), "[settingsBadRetention]");
});

test("the deny list drops blanks, duplicates, leading dots and wildcards", async () => {
  realm("");
  const { parseDomains } = await load();
  assert.deepEqual(
    parseDomains(" bank.test \n\n*.Shop.test\n.mail.test\nbank.test\n"),
    ["bank.test", "shop.test", "mail.test"],
  );
  assert.deepEqual(parseDomains(""), []);
});

// ------------------------------------------------------------------ 往返

const CONFIG = {
  confirm_mode: "per_domain",
  deny_domains: ["bank.test"],
  approved_domains: ["shop.test"],
  audit: true,
  audit_retention_days: 7,
  disabled_features: ["downloads"],
  domain_disabled_features: { "shop.test": ["script"] },
};

test("the form round-trips a config through render and readForm", async () => {
  realm(HTML);
  const { render, readForm } = await load();
  render(CONFIG, "/home/u/browse.yaml");

  assert.equal(document.getElementById("path")?.textContent, "/home/u/browse.yaml");
  assert.deepEqual(readForm(), {
    confirm_mode: "per_domain",
    deny_domains: ["bank.test"],
    audit: true,
    audit_retention_days: 7,
    disabled_features: ["downloads"],
    domain_disabled_features: { "shop.test": ["script"] },
  });
  // approved_domains 是只读的：设置页不把它当表单字段写回去
  const approved = document.getElementById("approved");
  assert.match(approved?.textContent ?? "", /shop\.test/);
  assert.match(approved?.textContent ?? "", /\[panelRevoke\]/);
});

test("功能目录每个功能都画出来了，方法名单也带上", async () => {
  realm(HTML);
  const { render, FEATURES } = await load();
  render(CONFIG, "");
  const rows = Array.from(document.querySelectorAll<HTMLDivElement>("#features .feature"));
  assert.equal(rows.length, FEATURES.length);
  for (const feature of FEATURES) {
    const row = document.querySelector(`.feature[data-feature="${feature.id}"]`);
    assert.ok(row, `功能 ${feature.id} 没画出来`);
    assert.ok(
      row?.querySelector("code")?.textContent?.includes(feature.methods[0]),
      `方法 ${feature.methods[0]} 没展示`,
    );
  }
  assert.equal(
    document.querySelector<HTMLInputElement>('.feature[data-feature="downloads"] .feat-off')?.checked,
    false,
    "禁用的功能不勾（勾 = 启用）",
  );
  assert.equal(
    document.querySelector<HTMLInputElement>('.feature[data-feature="tabs"] .feat-off')?.checked,
    true,
    "默认全部启用，勾上",
  );
  assert.equal(
    document.querySelector<HTMLInputElement>('.feature[data-feature="script"] .feat-domains')?.value,
    "shop.test",
    "按域名禁用的域名要填回去",
  );
});

test("空格和逗号分隔的域名也能解析，不只是换行", async () => {
  realm("");
  const { parseDomains } = await load();
  assert.deepEqual(parseDomains("a.test, b.test;c.test  d.test"), [
    "a.test",
    "b.test",
    "c.test",
    "d.test",
  ]);
});

test("the real path is always on the page, so nobody thinks this is a second config", async () => {
  assert.match(HTML, /id="path"/);
  assert.match(HTML, /data-i18n="settingsIntro"/);
});

// ------------------------------------------------- 本地程序没起来也要能用（起点）

test("daemon 一句话都不用说：设置页能读", async () => {
  // realm() 里**没有** chrome.runtime.sendMessage。settings.ts 只要碰它一下就会
  // TypeError，这条测试就红了 —— 这正是「不依赖本地程序」想钉死的东西。
  realm(HTML, { "browse:config": CONFIG });
  await boot();

  assert.equal(document.body.classList.contains("offline"), false, "没有理由显示离线");
  assert.equal(
    document.querySelector<HTMLInputElement>('input[name="confirm_mode"]:checked')?.value,
    "per_domain",
  );
  assert.match(document.getElementById("path")?.textContent ?? "", /browse:config/);
});

test("daemon 一句话都不用说：设置页能改能存，存完读回来还在", async () => {
  const { data, dom } = realm(HTML, { "browse:config": CONFIG });
  const mod = await boot();

  const deny = document.getElementById("deny") as HTMLTextAreaElement;
  deny.value = "evil.test\nbank.test";
  (document.getElementById("audit") as HTMLInputElement).checked = false;
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  const saved = data["browse:config"] as {
    deny_domains: string[];
    audit: boolean;
    approved_domains: string[];
  };
  assert.deepEqual(saved.deny_domains, ["evil.test", "bank.test"], "改动必须真的落盘");
  assert.equal(saved.audit, false);
  assert.deepEqual(saved.approved_domains, ["shop.test"], "没碰的字段要原样留着");
  assert.equal(document.getElementById("status")?.textContent, "[settingsSaved]");
  assert.equal(mod.CONFIRM_MODES.length, 3);
});

test("没存过配置时给的是默认值，不是一片空白", async () => {
  realm(HTML, {});
  await boot();

  assert.equal(
    document.querySelector<HTMLInputElement>('input[name="confirm_mode"]:checked')?.value,
    "silent",
    "默认模式",
  );
  assert.equal((document.getElementById("retention") as HTMLInputElement).value, "7");
  assert.equal(document.body.classList.contains("offline"), false);
});

test("撤销一个免确认域名，直接写存储，不经任何中间人", async () => {
  const { data, dom } = realm(HTML, { "browse:config": CONFIG });
  await boot();

  const button = document.querySelector<HTMLButtonElement>("#approved button");
  assert.ok(button, "应该有一个撤销按钮");
  button.dispatchEvent(new dom.window.Event("click"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(
    (data["browse:config"] as { approved_domains: string[] }).approved_domains,
    [],
  );
});

test("插件自己的存储坏了才显示离线，并且不画一份空配置上去", async () => {
  realm(HTML, {});
  (globalThis as Any).chrome.storage.local.get = async () => {
    throw new Error("storage is gone");
  };
  await boot();

  assert.equal(document.body.classList.contains("offline"), true, "表单必须被禁掉");
  assert.equal(document.getElementById("status")?.className, "bad");
  assert.equal(document.getElementById("path")?.textContent, "…", "不能让用户以为设置丢了");
});

// ------------------------------------------------------------------ 打包

test("settings.html and settings.ts are in the build, and the manifest points at the page", () => {
  const build = readFileSync(join(SRC, "..", "build.mjs"), "utf8");
  assert.match(build, /"src\/settings\.ts"/, "settings.ts 没进 entryPoints");
  assert.match(build, /"settings\.html"/, "settings.html 没被拷进 dist");
  const manifest = JSON.parse(readFileSync(join(SRC, "manifest.json"), "utf8"));
  assert.equal(manifest.options_page, "settings.html");
  assert.equal(manifest.default_locale, "zh_CN");
});

test("审计日志的用量计数异步补到每个功能行上", async () => {
  realm(HTML, {
    "browse:config": CONFIG,
    "browse:audit": [
      { method: "script.evaluate", ok: true },
      { method: "script.evaluate", ok: true },
      { method: "input.click", ok: true },
      { method: "no.such", ok: true },
    ],
  });
  await boot();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 1));
  const usage = document.querySelector<HTMLSpanElement>('.feature[data-feature="script"] .usage');
  assert.match(usage?.textContent ?? "", /settingsFeatUsed:2/);
  const clickUsage = document.querySelector<HTMLSpanElement>('.feature[data-feature="input"] .usage');
  assert.ok((clickUsage?.textContent ?? "").length > 0, "input 功能行也要有计数");
});

test("没有对应节点时 render 的每条守卫都安全返回", async () => {
  realm("");
  const { render, renderApproved } = await load();
  render(CONFIG, "");
  // renderApproved 未导出，但 render→renderApproved(domains) 已在无 #approved 的
  // body 里跑过守卫；这里再验证没抛、没写任何东西
  assert.equal(document.body.children.length, 0);
});

test("表单校验失败时 save 只说不存", async () => {
  const { dom } = realm(HTML, { "browse:config": CONFIG });
  await boot();
  (document.getElementById("retention") as HTMLInputElement).value = "abc"; // parseInt→NaN，才进得了校验分支
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(document.getElementById("status")?.className, "bad");
  assert.match(document.getElementById("status")?.textContent ?? "", /settingsBadRetention/);
});

test("存储写不进去时撤销和保存都把错误摆到状态行", async () => {
  const { dom } = realm(HTML, { "browse:config": CONFIG });
  (globalThis as Any).chrome.storage.local.set = async () => {
    throw new Error("quota full");
  };
  await boot();

  document.querySelector<HTMLButtonElement>("#approved button")
    ?.dispatchEvent(new dom.window.Event("click"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(document.getElementById("status")?.textContent ?? "", /quota full/);

  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(document.getElementById("status")?.textContent, "quota full");
});

test("readForm 在功能行不在页面上时按空名单返回", async () => {
  // realm("") 的 body 没有 .feature 行 → readForm 的 continue 分支
  realm("");
  const { readForm } = await load();
  assert.deepEqual(readForm(), {
    confirm_mode: "",
    deny_domains: [],
    audit: false,
    audit_retention_days: Number.NaN,
    disabled_features: [],
    domain_disabled_features: {},
  });
});
