/**
 * 设置页。测的是「能做歪的地方」：
 *
 * - **daemon 没连上时照样能读能改能存** —— 用户提这整件事的起点就是这条
 * - 非法值有没有在扩展侧就被挡住
 * - `settings.html` 有没有被打包进 dist（漏了线上就是 404，本地开着 src/ 看不出来）
 *
 * settings.ts 在 `#form` 存在时导入就会自举（load + 事件绑定），且 Node 的覆盖率
 * 不聚合带 query 的多次导入。规矩：**本进程只无 query 地导入一次**，首个用例先摆好
 * 完整表单再导入让自举跑在真表单上，保存类场景共用这个自举 realm；其它场景换 realm
 * 后直接调导出的函数。需要另一种自举结果的用例各占一个文件
 * （settings-boot.test.ts / settings-boot2.test.ts）。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

const SRC = join(import.meta.dirname, "..", "src");
const HTML = readFileSync(join(SRC, "settings.html"), "utf8");

type Any = Record<string, unknown>;

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

let mod: Any | null = null;
let bootDoc: Document | null = null;

async function load() {
  mod ??= await import("../src/settings.ts");
  return mod;
}

function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// ------------------------------------------------------------------ 自举（先跑）

const CONFIG = {
  confirm_mode: "per_domain",
  deny_domains: ["bank.test"],
  approved_domains: ["shop.test"],
  audit: true,
  audit_retention_days: 7,
  disabled_features: ["downloads"],
  domain_disabled_features: { "shop.test": ["script"] },
};

test("daemon 一句话都不用说：设置页能读、能改能存、坏草稿被拒、写失败报错", async () => {
  // 自举只跑一次（绑在这次导入时的表单上），读/存/拒存/写失败四段共用一个 realm。
  // realm() 里**没有** chrome.runtime.sendMessage。settings.ts 只要碰它一下就会
  // TypeError，这条测试就红了 —— 这正是「不依赖本地程序」想钉死的东西。
  const { data, dom } = realm(HTML, { "browse:config": CONFIG });
  const m = await load();
  await settle();
  bootDoc = dom.window.document;

  assert.equal(document.body.classList.contains("offline"), false, "没有理由显示离线");
  assert.equal(
    document.querySelector<HTMLInputElement>('input[name="confirm_mode"]:checked')?.value,
    "per_domain",
  );
  assert.match(document.getElementById("path")?.textContent ?? "", /browse:config/);
  assert.equal(m.CONFIRM_MODES.length, 3);

  // 改动真的落盘，没碰的字段原样留着
  const deny = document.getElementById("deny") as HTMLTextAreaElement;
  deny.value = "evil.test\nbank.test";
  (document.getElementById("audit") as HTMLInputElement).checked = false;
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await settle();

  const saved = data["browse:config"] as {
    deny_domains: string[];
    audit: boolean;
    approved_domains: string[];
  };
  assert.deepEqual(saved.deny_domains, ["evil.test", "bank.test"], "改动必须真的落盘");
  assert.equal(saved.audit, false);
  assert.deepEqual(saved.approved_domains, ["shop.test"], "没碰的字段要原样留着");
  assert.equal(document.getElementById("status")?.textContent, "[settingsSaved]");

  // 确认模式一个都没选：拒绝保存，不悄悄填成最松的模式
  const before = { ...saved };
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="confirm_mode"]')) {
    input.checked = false;
  }
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await settle();
  assert.equal(document.getElementById("status")?.textContent, "[settingsBadMode:]");
  assert.deepEqual(data["browse:config"], before, "坏草稿不许落盘");

  // 写失败（Error）：状态行报错不炸页。先把上一段取消勾选的模式勾回来
  const perDomain = document.querySelector<HTMLInputElement>(
    'input[name="confirm_mode"][value="per_domain"]',
  );
  if (perDomain !== null) {
    perDomain.checked = true;
  }
  (globalThis as Any).chrome.storage.local.set = async () => {
    throw new Error("save boom");
  };
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await settle();
  assert.equal(document.getElementById("status")?.textContent, "save boom");

  // 写失败（非 Error 异常）：也要给出可读的话
  (globalThis as Any).chrome.storage.local.set = async () => {
    throw "plain";
  };
  document.getElementById("form")?.dispatchEvent(new dom.window.Event("submit"));
  await settle();
  assert.equal(document.getElementById("status")?.textContent, "plain");
});

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
  assert.deepEqual(parseDomains("a.test, b.test;c.test  d.test"), [
    "a.test",
    "b.test",
    "c.test",
    "d.test",
  ]);
});

// ------------------------------------------------------------------ 往返

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

test("the real path is always on the page, so nobody thinks this is a second config", () => {
  assert.match(HTML, /id="path"/);
  assert.match(HTML, /data-i18n="settingsIntro"/);
});

// ------------------------------------------------- 边角：空页、审计计数、写失败

test("页面上没有那些 id 时 render/readForm 安静返回，不抛", async () => {
  realm("");
  const { render, readForm } = await load();
  assert.doesNotThrow(() => render(CONFIG, ""));
  // 没有功能行：readForm 的 continue 分支要走通，返回空列表而不是抛
  var draft = readForm();
  assert.deepEqual(draft.disabled_features, []);
  assert.deepEqual(draft.domain_disabled_features, {});
});

test("审计计数异步补：认识的按方法计数，不认识的丢掉；审计读坏了页面照常", async () => {
  const entries = [
    { ts: "2026-01-01T00:00:00Z", method: "script.evaluate", domain: "a.test", action: null, result: "success", ms: 1 },
    { ts: "2026-01-01T00:00:01Z", method: "script.evaluate", domain: "b.test", action: null, result: "success", ms: 1 },
    { ts: "2026-01-01T00:00:02Z", method: "no.such.method", domain: null, action: null, result: "success", ms: 1 },
  ];
  realm(HTML, { "browse:audit": entries });
  const { render } = await load();
  render(CONFIG, "");
  await settle();

  const used = document.querySelector<HTMLSpanElement>('.feature[data-feature="script"] .usage');
  assert.match(used?.textContent ?? "", /settingsFeatUsed:2/);
});

test("审计读坏了：设置页照常打开，只是次数那格不显示", async () => {
  realm(HTML, {});
  const { render } = await load();
  (globalThis as Any).chrome.storage.local.get = async (key: string) => {
    if (key === "browse:audit") {
      throw new Error("audit gone");
    }
    return {};
  };
  assert.doesNotThrow(() => render(CONFIG, ""));
  await settle();
  assert.match(
    document.querySelector<HTMLSpanElement>('.feature[data-feature="script"] .usage')?.textContent ?? "",
    /settingsFeatUnused/,
  );
});

test("免确认名单为空时给一句「还没有」", async () => {
  realm(HTML);
  const { render } = await load();
  render({ ...CONFIG, approved_domains: [] }, "");
  assert.match(document.getElementById("approved")?.textContent ?? "", /settingsApprovedEmpty/);
});

test("撤销成功直接写存储；撤销失败在页面上报错，不炸整页", async () => {
  const { data } = realm(HTML, { "browse:config": CONFIG });
  const { render } = await load();
  render(CONFIG, "");
  await settle();
  (document.querySelector<HTMLButtonElement>("#approved button"))?.click();
  await settle();
  assert.deepEqual(
    (data["browse:config"] as { approved_domains: string[] }).approved_domains,
    [],
  );

  (globalThis as Any).chrome.storage.local.set = async () => {
    throw new Error("set boom");
  };
  render(CONFIG, "");
  (document.querySelector<HTMLButtonElement>("#approved button"))?.click();
  await settle();
  assert.equal(document.getElementById("status")?.textContent, "set boom");
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

// bootDoc 供需要回到自举 realm 的后续文件参考（本文件内不再使用则忽略）
void bootDoc;
