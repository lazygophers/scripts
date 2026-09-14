/**
 * 设置页。测的是「能做歪的地方」：非法值有没有在扩展侧就被挡住、daemon 连不上时
 * 会不会静默显示一份空配置、以及 `settings.html` 有没有被打包进 dist（漏了线上就是
 * 404，而本地开着 src/ 看不出来）。
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
function realm(body: string, sent: Any[] = [], replies: Any[] = []): void {
  const dom = new JSDOM(`<!doctype html><body>${body}</body>`);
  const g = globalThis as Any;
  g.document = dom.window.document;
  g.HTMLElement = dom.window.HTMLElement;
  g.chrome = {
    i18n: {
      getUILanguage: () => "zh-CN",
      getMessage: (key: string, args?: string[]) => `[${key}${args?.length ? ":" + args.join(",") : ""}]`,
    },
    runtime: {
      sendMessage: async (message: Any) => {
        sent.push(message);
        return replies.shift() ?? { ok: true };
      },
    },
  };
}

afterEach(() => {
  const g = globalThis as Any;
  delete g.document;
  delete g.HTMLElement;
  delete g.chrome;
});

async function load() {
  // 每个用例要拿到新的模块实例（自举那段只在导入时跑一次），所以带 query 破缓存
  return import(`../src/settings.ts?${Math.random()}`);
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
  });
  // approved_domains 是只读的：设置页不把它当表单字段写回去
  const approved = document.getElementById("approved");
  assert.match(approved?.textContent ?? "", /shop\.test/);
  assert.match(approved?.textContent ?? "", /\[panelRevoke\]/);
});

test("the real path is always on the page, so nobody thinks this is a second config", async () => {
  assert.match(HTML, /id="path"/);
  assert.match(HTML, /data-i18n="settingsIntro"/);
});

// ------------------------------------------------------------------ 降级

test("with the daemon unreachable the page says so and does not show an empty config", async () => {
  const sent: Any[] = [];
  realm(HTML, sent, [{ ok: false, error: "daemon not connected", state: "disconnected" }]);
  await load();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(document.body.classList.contains("offline"), true, "表单必须被禁掉");
  assert.equal(document.getElementById("status")?.textContent, "daemon not connected");
  assert.equal(document.getElementById("status")?.className, "bad");
  // 没有把一份空配置画上去 —— 那会让用户以为设置丢了
  assert.equal(document.getElementById("path")?.textContent, "…");
  assert.deepEqual(sent, [{ type: "browse-config", op: "get", config: undefined }]);
});

test("a good load fills the form and clears the offline banner", async () => {
  const sent: Any[] = [];
  realm(HTML, sent, [{ ok: true, config: CONFIG, path: "/tmp/browse.yaml", state: "connected" }]);
  await load();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(document.body.classList.contains("offline"), false);
  assert.equal(document.getElementById("path")?.textContent, "/tmp/browse.yaml");
  assert.equal(
    document.querySelector<HTMLInputElement>('input[name="confirm_mode"]:checked')?.value,
    "per_domain",
  );
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
