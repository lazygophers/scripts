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
 *    用户自己打开「强制拦截」才拦，拦法见 `intercept.ts`。
 */

import { applyRules } from "./intercept.ts";
import {
  DEFAULT_CUSTOM,
  DEFAULT_PALETTE,
  DEFAULT_STYLE,
  FIELDS,
  PALETTES,
  STYLES,
  applyTheme,
  contrast,
  hexOf,
  type Custom,
  type Palette,
  type Selection,
  type TokenName,
} from "./themes.ts";

/** 设置存在扩展自己的存储里，浏览器重启照样在。 */
const KEY = "viewer-settings";

export type Settings = {
  /** 会被浏览器下载的本地文本文件，改为在扩展自己的展示页里打开。默认关。 */
  force: boolean;
  /** 选中的配色。`"custom"` 表示用下面那套自己调的。 */
  theme: Selection;
  /** 选中的风格（版面）。配色和风格互相独立，可以任意搭。 */
  style: string;
  /** 自己调的那套：以哪套配色为底子 + 改了哪几个值。 */
  custom: Custom;
};

const DEFAULTS: Settings = {
  force: false,
  theme: DEFAULT_PALETTE,
  style: DEFAULT_STYLE,
  custom: DEFAULT_CUSTOM,
};

export async function readSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get(KEY);
  return { ...DEFAULTS, ...((stored[KEY] as Partial<Settings> | undefined) ?? {}) };
}

/**
 * 写操作排队。
 *
 * 每次写都是「先读回整份设置、合并、再写回」，两次写挨太近时第二次会读到第一次落盘前的
 * 旧值，把前一次的改动覆盖掉（连着点「配色」再点「风格」就能复现）。串成一条链之后，
 * 后一次一定读到前一次的结果。
 */
let queue: Promise<unknown> = Promise.resolve();

export function writeSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = queue.then(() => writeNow(patch));
  queue = next.catch(() => undefined);
  return next;
}

async function writeNow(patch: Partial<Settings>): Promise<Settings> {
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
 * 主题那一节：配色八张卡 + 风格四张卡 + 自定义 + 规格栏。
 *
 * 设置页自己也应用当前主题，所以页面上那块预览就是真实效果，不是另画一份模拟。
 */
export async function renderThemes(doc: Document): Promise<void> {
  const settings = await readSettings();
  applyTheme(doc, settings.theme, settings.style, settings.custom);

  fillCards(
    doc,
    "lfv-palettes",
    [
      ...PALETTES.map((p) => ({ id: p.id, name: p.name, hint: p.hint, palette: p })),
      {
        id: "custom",
        name: "自定义",
        hint: "以某套配色为底子，自己改颜色",
        palette: PALETTES.find((p) => p.id === settings.custom.base) ?? PALETTES[0]!,
      },
    ],
    settings.theme,
    (id) => void writeSettings({ theme: id }).then(() => renderThemes(doc)),
  );

  fillCards(
    doc,
    "lfv-styles",
    STYLES.map((s) => ({ id: s.id, name: s.name, hint: s.hint })),
    settings.style,
    (id) => void writeSettings({ style: id }).then(() => renderThemes(doc)),
  );

  (doc.getElementById("lfv-custom") as HTMLElement).hidden = settings.theme !== "custom";
  if (settings.theme === "custom") renderCustom(doc, settings.custom);
  renderSpec(doc);
}

type Card = { id: string; name: string; hint: string; palette?: Palette };

/** 一组卡片：左边一块小样、右边名字和一句说明。配色的小样用它自己的颜色画。 */
function fillCards(
  doc: Document,
  hostId: string,
  cards: Card[],
  current: string,
  pick: (id: string) => void,
): void {
  const host = doc.getElementById(hostId) as HTMLElement;
  host.replaceChildren(
    ...cards.map((card) => {
      const node = doc.createElement("button");
      node.type = "button";
      node.className = "lfv-theme";
      node.dataset["theme"] = card.id;
      node.setAttribute("aria-pressed", String(card.id === current));

      const chip = doc.createElement("span");
      chip.className = "lfv-chip";
      chip.textContent = "文";
      if (card.palette) {
        // 小样按那套配色自己的公式画，不受当前主题影响——否则八张卡长得一模一样。
        const { h, c, polarity } = card.palette;
        const [bg, fg] = polarity === "light" ? [97.5, 27] : [21, 89];
        chip.setAttribute(
          "style",
          `background: oklch(${bg}% ${0.018 * c} ${h}); color: oklch(${fg}% ${0.022 * c} ${h})`,
        );
      }

      const text = doc.createElement("span");
      const title = doc.createElement("span");
      title.className = "lfv-name";
      title.textContent = card.name;
      const note = doc.createElement("span");
      note.className = "lfv-hint";
      note.textContent = card.hint;
      text.append(title, note);

      node.append(chip, text);
      node.addEventListener("click", () => pick(card.id));
      return node;
    }),
  );
}

/**
 * 规格栏：当前主题的六档语法色、真实十六进制值、正文对比度。
 *
 * 只在设置页出现，文件页上不显示（用户 2026-09-19 选定：一天开几十次文件页，
 * 色号一个月看一次，不值得每天为它付一条宽度）。
 */
function renderSpec(doc: Document): void {
  const style = doc.defaultView?.getComputedStyle(doc.documentElement);
  if (!style) return;
  const read = (name: string) => hexOf(doc, style.getPropertyValue(`--${name}`).trim());

  const rows = SPEC_ROWS.map(([token, label]) => {
    const hex = read(token);
    const swatch = doc.createElement("i");
    swatch.setAttribute("style", `background:${hex}`);
    const row = doc.createElement("div");
    const name = doc.createElement("span");
    name.textContent = label;
    const code = doc.createElement("code");
    code.textContent = hex.toUpperCase();
    row.append(swatch, name, code);
    return row;
  });
  (doc.getElementById("lfv-spec-rows") as HTMLElement).replaceChildren(...rows);

  const ratio = contrast(read("foreground"), read("background"));
  const meter = doc.getElementById("lfv-spec-contrast") as HTMLElement;
  meter.textContent = `正文 / 底色 ${ratio.toFixed(2)}:1（可读的下限是 4.5:1）`;
  meter.dataset["state"] = ratio >= 4.5 ? "on" : "off";
}

const SPEC_ROWS: readonly [string, string][] = [
  ["syn-comment", "注释 comment"],
  ["syn-keyword", "关键字 keyword"],
  ["syn-string", "字符串 string"],
  ["syn-number", "数字 number"],
  ["syn-function", "函数名 function"],
  ["syn-type", "类型名 type"],
];

/** 自定义那一块：底子选择 + 十四个颜色输入，改一下立刻生效。 */
function renderCustom(doc: Document, custom: Custom): void {
  const patch = (key: TokenName, next: string) =>
    void writeSettings({ custom: { ...custom, patch: { ...custom.patch, [key]: next } } }).then(
      () => renderThemes(doc),
    );

  const select = doc.getElementById("lfv-base") as HTMLSelectElement;
  select.replaceChildren(
    ...PALETTES.map((palette) => {
      const option = doc.createElement("option");
      option.value = palette.id;
      option.textContent = palette.name;
      option.selected = palette.id === custom.base;
      return option;
    }),
  );
  select.onchange = () =>
    void writeSettings({ custom: { ...custom, base: select.value } }).then(() => renderThemes(doc));

  const style = doc.defaultView?.getComputedStyle(doc.documentElement);
  const colors = doc.getElementById("lfv-colors") as HTMLElement;
  colors.replaceChildren(
    ...FIELDS.map(({ key, label }) => {
      const field = doc.createElement("label");
      field.className = "lfv-field";
      field.append(label);
      const input = doc.createElement("input");
      input.type = "color";
      // 没改过的项显示底子算出来的那个色，而不是空白——你看到什么就是在改什么。
      input.value =
        custom.patch[key] ?? (style ? hexOf(doc, style.getPropertyValue(`--${key}`).trim()) : "#000000");
      // `input` 而不是 `change`：拖着取色盘时页面就跟着变。
      input.addEventListener("input", () => patch(key, input.value));
      field.append(input);
      return field;
    }),
  );

  (doc.getElementById("lfv-reset") as HTMLElement).onclick = () =>
    void writeSettings({ custom: { ...custom, patch: {} } }).then(() => renderThemes(doc));
}
