/**
 * 设置页，兼装好扩展之后的欢迎页（地址带 `?welcome=1` 时多出上手说明那一段）。
 *
 * 只有两件事要交代给用户：
 *
 * 1. 「允许访问文件网址」这个开关必须用户自己去浏览器里打开，扩展代劳不了；
 *    但扩展读得到它的真实状态（`chrome.extension.isAllowedFileSchemeAccess()`，
 *    <https://developer.chrome.com/docs/extensions/reference/api/extension#method-isAllowedFileSchemeAccess>），
 *    所以页面上写的是实况，不是「大概没开吧」。
 * 2. 有几类文件浏览器会直接下载，viewer 根本没机会出手。默认不管这件事，
 *    用户自己打开「强制拦截」才拦，拦法见 `rules.ts`。
 */

import { applyRules } from "./rules.ts";
import {
  COLOR_FIELDS,
  DEFAULT_CUSTOM,
  DEFAULT_THEME,
  THEMES,
  TYPE_FIELDS,
  applyTheme,
  numberOf,
  resolve,
  type Custom,
  type Selection,
  type Tokens,
} from "./themes.ts";

/** 设置存在扩展自己的存储里，浏览器重启照样在。 */
const KEY = "viewer-settings";

export type Settings = {
  /** 会被浏览器下载的本地文本文件，改为在扩展自己的展示页里打开。默认关。 */
  force: boolean;
  /** 选中的主题。`"custom"` 表示用下面那套自己调的。 */
  theme: Selection;
  /** 自己调的那套：以哪套为底子 + 改了哪几个值。 */
  custom: Custom;
};

const DEFAULTS: Settings = { force: false, theme: DEFAULT_THEME, custom: DEFAULT_CUSTOM };

export async function readSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get(KEY);
  return { ...DEFAULTS, ...((stored[KEY] as Partial<Settings> | undefined) ?? {}) };
}

export async function writeSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await readSettings()), ...patch };
  await chrome.storage.local.set({ [KEY]: next });
  // 拦截规则跟着开关走，改完立刻生效，不用重装扩展。
  if (patch.force !== undefined) await applyRules(next.force);
  return next;
}

/**
 * 设置一变就回调，用来让**已经开着的那些页面**跟着换主题。
 *
 * 主题是「我喜欢什么」而不是「这一页什么样」，所以是全局的：在任意一页切一次，
 * 其余标签页立刻跟上（用户 2026-09-18 选定）。`chrome.storage` 的变更事件本来就
 * 跨标签页广播，不用自己做消息通道。
 */
export function onSettingsChange(handler: (settings: Settings) => void): void {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    const change = changes[KEY];
    if (change === undefined) return;
    handler({ ...DEFAULTS, ...((change.newValue as Partial<Settings> | undefined) ?? {}) });
  });
}

/** 浏览器里那个「允许访问文件网址」开关现在是开是关。 */
export async function fileAccess(): Promise<boolean> {
  // Firefox 没有这个方法：那边扩展本来就读得到本地文件，等于一直是开着的。
  if (typeof chrome.extension?.isAllowedFileSchemeAccess !== "function") return true;
  return chrome.extension.isAllowedFileSchemeAccess();
}

/**
 * 把页面接上：权限状态写成实况，开关读存储、改存储。
 *
 * 页面骨架写在 `settings.html` 里，这里只填内容和挂事件——这样测试给一份同样 id 的
 * 骨架就能跑，不必把一堆 `createElement` 塞进来。
 */
export async function renderSettings(doc: Document): Promise<void> {
  const allowed = await fileAccess();
  const status = doc.getElementById("lfv-access") as HTMLElement;
  status.textContent = allowed ? "已经打开，本地文件可以正常显示" : "还没打开，本地文件显示不了";
  status.dataset["state"] = allowed ? "on" : "off";

  // 已经打开的用户不必再看那一串步骤，只有真的没打开时才摊开。
  // Firefox 没有这个开关，也就没有 `isAllowedFileSchemeAccess`，那边照着 Chrome 的步骤
  // 走只会扑空，所以换一句话说明。
  const chromium = typeof chrome.extension?.isAllowedFileSchemeAccess === "function";
  (doc.getElementById("lfv-steps") as HTMLElement).hidden = allowed || !chromium;
  (doc.getElementById("lfv-firefox") as HTMLElement).hidden = chromium;

  // 装好之后自动弹出来的那一次多一句欢迎话；从菜单里点进来的就只是设置页。
  const hello = doc.getElementById("lfv-welcome") as HTMLElement;
  hello.hidden = !new URL(doc.URL).searchParams.has("welcome");

  const force = doc.getElementById("lfv-force") as HTMLInputElement;
  force.checked = (await readSettings()).force;
  force.addEventListener("change", () => {
    void writeSettings({ force: force.checked });
  });

  await renderThemes(doc);
}

/**
 * 主题那一节：四张卡 + 自定义 + 预览。
 *
 * 设置页自己也应用当前主题，所以下面那块预览就是真实效果，不是另画一份模拟。
 */
export async function renderThemes(doc: Document): Promise<void> {
  const settings = await readSettings();
  applyTheme(doc, settings.theme, settings.custom);

  const cards = doc.getElementById("lfv-themes") as HTMLElement;
  const rows: { id: Selection; name: string; hint: string }[] = [
    ...THEMES.map((theme) => ({ id: theme.id as Selection, name: theme.name, hint: theme.hint })),
    { id: "custom", name: "自定义", hint: "以现成的一套为底子，自己改颜色和排版" },
  ];
  cards.replaceChildren(
    ...rows.map(({ id, name, hint }) => {
      const card = doc.createElement("button");
      card.type = "button";
      card.className = "lfv-theme";
      card.dataset["theme"] = id;
      card.setAttribute("aria-pressed", String(id === settings.theme));

      // 小样按那套主题自己的颜色画，不受当前主题影响——否则四张卡长得一模一样。
      const preview = resolve(id, settings.custom);
      const chip = doc.createElement("span");
      chip.className = "lfv-chip";
      chip.textContent = "文";
      chip.setAttribute(
        "style",
        `background:${preview.tokens.background};color:${preview.tokens.foreground};` +
          `font-family:${preview.tokens["lfv-font"]}`,
      );

      const text = doc.createElement("span");
      const title = doc.createElement("span");
      title.className = "lfv-name";
      title.textContent = name;
      const note = doc.createElement("span");
      note.className = "lfv-hint";
      note.textContent = hint;
      text.append(title, note);

      card.append(chip, text);
      card.addEventListener("click", () => {
        void writeSettings({ theme: id }).then(() => renderThemes(doc));
      });
      return card;
    }),
  );

  (doc.getElementById("lfv-custom") as HTMLElement).hidden = settings.theme !== "custom";
  if (settings.theme === "custom") renderCustom(doc, settings.custom);
}

/** 自定义那一块：底子选择 + 颜色输入 + 排版滑块，改一下立刻生效。 */
function renderCustom(doc: Document, custom: Custom): void {
  const base = resolve(custom.base).tokens;
  const value = (key: keyof Tokens): string => custom.patch[key] ?? base[key];
  const patch = (key: keyof Tokens, next: string) =>
    void writeSettings({ custom: { ...custom, patch: { ...custom.patch, [key]: next } } }).then(
      () => renderThemes(doc),
    );

  const select = doc.getElementById("lfv-base") as HTMLSelectElement;
  select.replaceChildren(
    ...THEMES.map((theme) => {
      const option = doc.createElement("option");
      option.value = theme.id;
      option.textContent = theme.name;
      option.selected = theme.id === custom.base;
      return option;
    }),
  );
  select.onchange = () =>
    void writeSettings({ custom: { ...custom, base: select.value as Custom["base"] } }).then(() =>
      renderThemes(doc),
    );

  const colors = doc.getElementById("lfv-colors") as HTMLElement;
  colors.replaceChildren(
    ...COLOR_FIELDS.map(({ key, label }) => {
      const field = doc.createElement("label");
      field.className = "lfv-field";
      field.append(label);
      const input = doc.createElement("input");
      input.type = "color";
      input.value = value(key);
      // `input` 而不是 `change`：拖着取色盘时就能看到页面跟着变。
      input.addEventListener("input", () => patch(key, input.value));
      field.append(input);
      return field;
    }),
  );

  const type = doc.getElementById("lfv-type") as HTMLElement;
  type.replaceChildren(
    ...TYPE_FIELDS.map(({ key, label, min, max, step, unit }) => {
      const field = doc.createElement("label");
      field.className = "lfv-field";
      field.append(label);
      const input = doc.createElement("input");
      input.type = "range";
      input.min = String(min);
      input.max = String(max);
      input.step = String(step);
      const now = numberOf(value(key), min);
      input.value = String(now);
      const shown = doc.createElement("span");
      shown.className = "lfv-value";
      shown.textContent = `${now}${unit}`;
      input.addEventListener("input", () => patch(key, `${input.value}${unit}`));
      field.append(input, shown);
      return field;
    }),
  );

  (doc.getElementById("lfv-reset") as HTMLElement).onclick = () =>
    void writeSettings({ custom: { ...custom, patch: {} } }).then(() => renderThemes(doc));
}
