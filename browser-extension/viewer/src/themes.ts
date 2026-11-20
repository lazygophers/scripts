/**
 * 主题 = **配色 × 风格** 两层（2026-09-19 定稿，第二轮设计）。
 *
 * 第一轮把主题做成「一套颜色 + 一套排版」捆在一起，用户否掉了全部六项。第二轮重做，
 * 拆成互相独立的两层：
 *
 * - **配色（palette）**：原有八套使用统一的 OKLCH 公式；新增 11 套 Atom One、Gruvbox、Ayu
 *   主题逐项覆盖来源色值。墨水屏仍是原有 `c: 0` 公式色板。
 *
 * - **风格（style）**：四套。它换的不是颜色，是**版面本身**：字体、字号、行距、每行字数、
 *   标题的大小关系、有没有规则线、圆角有没有、代码块怎么摆。用户原话：「主题不是单纯
 *   颜色的变化」。风格的实现全在 CSS（`html[data-lfv-style="…"]` 那几段），这里只记
 *   名字和说明。
 *
 * 19 套配色 × 四种风格 = 76 种组合；新主题只维护来源语义 token 覆盖。
 */

/** 一套配色。`h` 是色相角（0-360），`c` 是彩度系数（0 = 纯灰阶）。 */
export type Palette = {
  id: string;
  name: string;
  /** 一句话说它是什么调子，设置页里显示。 */
  hint: string;
  /** 亮面还是暗面。决定用哪条明度阶梯，也决定浏览器的滚动条跟着变亮还是变暗。 */
  polarity: "light" | "dark";
  h: number;
  c: number;
  /** Exact source colors; absent for the formula-based built-in palettes. */
  tokens?: Partial<Record<TokenName, string>>;
  source?: { label: string; url: string; license: string };
};

/** 原有八套后追加上游变体；数组顺序就是菜单和设置页里的顺序。 */
export const PALETTES: readonly Palette[] = [
  { id: "brass", name: "黄铜", hint: "暖纸调，白天读长文最耐看", polarity: "light", h: 85, c: 1 },
  { id: "ochre", name: "赭石", hint: "陶土色，亮面里带彩最重的一套", polarity: "light", h: 40, c: 1.15 },
  { id: "mauve", name: "藕合", hint: "冷粉灰，亮面里唯一的冷调", polarity: "light", h: 320, c: 0.9 },
  { id: "eink", name: "墨水屏·亮", hint: "纯灰阶、对比最高，语法靠字形分", polarity: "light", h: 0, c: 0 },
  { id: "pool", name: "深潭", hint: "青黑，暗面的基准色", polarity: "dark", h: 200, c: 1 },
  { id: "soot", name: "松烟", hint: "墨锭那种绿黑，比青黑暖", polarity: "dark", h: 155, c: 1 },
  { id: "night", name: "夜航", hint: "紫黑，暗面里最冷的一套", polarity: "dark", h: 275, c: 1.1 },
  { id: "einkd", name: "墨水屏·暗", hint: "纯黑底白字，暗面对比最高", polarity: "dark", h: 0, c: 0 },
  ...[
    { id: "one-dark", name: "One Dark", polarity: "dark", tokens: { background: "hsl(220 13% 18%)", card: "hsl(220 13% 21%)", foreground: "hsl(220 14% 71%)", muted: "hsl(220 13% 24%)", border: "hsl(220 9% 55%)", "muted-foreground": "hsl(220 10% 40%)", accent: "hsl(220 100% 66%)", warning: "hsl(39 67% 69%)", destructive: "hsl(0 70% 60%)", "syn-comment": "hsl(220 10% 40%)", "syn-keyword": "hsl(286 60% 67%)", "syn-string": "hsl(95 38% 62%)", "syn-number": "hsl(29 54% 61%)", "syn-function": "hsl(207 82% 66%)", "syn-type": "hsl(39 67% 69%)" } },
    { id: "one-light", name: "One Light", polarity: "light", tokens: { background: "hsl(230 1% 98%)", card: "hsl(230 1% 95%)", foreground: "hsl(230 8% 24%)", muted: "hsl(230 1% 91%)", border: "hsl(230 6% 44%)", "muted-foreground": "hsl(230 4% 64%)", accent: "hsl(230 100% 66%)", warning: "hsl(41 99% 38%)", destructive: "hsl(0 100% 54%)", "syn-comment": "hsl(230 4% 64%)", "syn-keyword": "hsl(301 63% 40%)", "syn-string": "hsl(119 34% 47%)", "syn-number": "hsl(41 99% 30%)", "syn-function": "hsl(221 87% 60%)", "syn-type": "hsl(41 99% 38%)" } },
    { id: "gruvbox-dark-hard", name: "Gruvbox Dark · hard", polarity: "dark", tokens: { background: "#1d2021", card: "#32302f", foreground: "#ebdbb2", muted: "#3c3836", border: "#8ec07c", "muted-foreground": "#928374", accent: "#8ec07c", warning: "#fabd2f", destructive: "#fb4934", "syn-comment": "#928374", "syn-keyword": "#fb4934", "syn-string": "#b8bb26", "syn-number": "#d3869b", "syn-function": "#b8bb26", "syn-type": "#fabd2f" } },
    { id: "gruvbox-dark", name: "Gruvbox Dark", polarity: "dark", tokens: { background: "#282828", card: "#32302f", foreground: "#ebdbb2", muted: "#3c3836", border: "#8ec07c", "muted-foreground": "#928374", accent: "#8ec07c", warning: "#fabd2f", destructive: "#fb4934", "syn-comment": "#928374", "syn-keyword": "#fb4934", "syn-string": "#b8bb26", "syn-number": "#d3869b", "syn-function": "#b8bb26", "syn-type": "#fabd2f" } },
    { id: "gruvbox-dark-soft", name: "Gruvbox Dark · soft", polarity: "dark", tokens: { background: "#32302f", card: "#3c3836", foreground: "#ebdbb2", muted: "#504945", border: "#8ec07c", "muted-foreground": "#928374", accent: "#8ec07c", warning: "#fabd2f", destructive: "#fb4934", "syn-comment": "#928374", "syn-keyword": "#fb4934", "syn-string": "#b8bb26", "syn-number": "#d3869b", "syn-function": "#b8bb26", "syn-type": "#fabd2f" } },
    { id: "gruvbox-light-hard", name: "Gruvbox Light · hard", polarity: "light", tokens: { background: "#f9f5d7", card: "#f2e5bc", foreground: "#3c3836", muted: "#ebdbb2", border: "#427b58", "muted-foreground": "#928374", accent: "#427b58", warning: "#b57614", destructive: "#9d0006", "syn-comment": "#928374", "syn-keyword": "#9d0006", "syn-string": "#79740e", "syn-number": "#8f3f71", "syn-function": "#79740e", "syn-type": "#b57614" } },
    { id: "gruvbox-light", name: "Gruvbox Light", polarity: "light", tokens: { background: "#fbf1c7", card: "#f2e5bc", foreground: "#3c3836", muted: "#ebdbb2", border: "#427b58", "muted-foreground": "#928374", accent: "#427b58", warning: "#b57614", destructive: "#9d0006", "syn-comment": "#928374", "syn-keyword": "#9d0006", "syn-string": "#79740e", "syn-number": "#8f3f71", "syn-function": "#79740e", "syn-type": "#b57614" } },
    { id: "gruvbox-light-soft", name: "Gruvbox Light · soft", polarity: "light", tokens: { background: "#f2e5bc", card: "#ebdbb2", foreground: "#3c3836", muted: "#d5c4a1", border: "#427b58", "muted-foreground": "#928374", accent: "#427b58", warning: "#b57614", destructive: "#9d0006", "syn-comment": "#928374", "syn-keyword": "#9d0006", "syn-string": "#79740e", "syn-number": "#8f3f71", "syn-function": "#79740e", "syn-type": "#b57614" } },
    { id: "ayu-dark", name: "Ayu Dark", polarity: "dark", tokens: { background: "#0F1419", card: "#14191F", foreground: "#E6E1CF", muted: "#1B2026", border: "#36A3D9", "muted-foreground": "#5C6773", accent: "#36A3D9", warning: "#E7C547", destructive: "#F07178", "syn-comment": "#5C6773", "syn-keyword": "#FF7733", "syn-string": "#B8CC52", "syn-number": "#FFEE99", "syn-function": "#FFB454", "syn-type": "#E6B673" } },
    { id: "ayu-mirage", name: "Ayu Mirage", polarity: "dark", tokens: { background: "#212733", card: "#272D38", foreground: "#D9D7CE", muted: "#2D3440", border: "#5CCFE6", "muted-foreground": "#5C6773", accent: "#5CCFE6", warning: "#E7C547", destructive: "#F07178", "syn-comment": "#5C6773", "syn-keyword": "#FFAE57", "syn-string": "#BBE67E", "syn-number": "#D4BFFF", "syn-function": "#FFD57F", "syn-type": "#FFC44C" } },
    { id: "ayu-light", name: "Ayu Light", polarity: "light", tokens: { background: "#FAFAFA", card: "#FFFFFF", foreground: "#5C6773", muted: "#F3F4F5", border: "#36A3D9", "muted-foreground": "#ABB0B6", accent: "#36A3D9", warning: "#E6B673", destructive: "#F07178", "syn-comment": "#ABB0B6", "syn-keyword": "#FF7733", "syn-string": "#86B300", "syn-number": "#A37ACC", "syn-function": "#F29718", "syn-type": "#E6B673" } },
  ].map((p) => ({ ...p, hint: "原作色值；界面补色复用邻近色，AA 未保证", h: 0, c: 0, source: { label: p.id.startsWith("one-") ? "Atom" : p.id.startsWith("gruvbox-") ? "Gruvbox" : "Ayu Vim", url: p.id.startsWith("one-") ? `https://github.com/atom/one-${p.id.slice(4)}-syntax/blob/master/LICENSE.md` : p.id.startsWith("gruvbox-") ? "https://github.com/morhetz/gruvbox/blob/master/README.md#license" : "https://github.com/ayu-theme/ayu-vim/blob/master/LICENSE", license: p.id.startsWith("one-") ? "MIT" : p.id.startsWith("gruvbox-") ? "MIT/X11" : "Apache-2.0" } } as Palette)),
];

/** 一种风格。换的是版面不是颜色，所以这里只有名字——实现全在 `viewer.css`。 */
export type Style = {
  id: string;
  name: string;
  hint: string;
};

export const STYLES: readonly Style[] = [
  {
    id: "manuscript",
    name: "文稿",
    hint: "中文衬线、行距 1.95、一行 34 字，标题大、留白多，读长文用",
  },
  {
    id: "press",
    name: "印刷",
    hint: "直角零圆角、1px 规则线分栏、章节自动编号，像一份印出来的东西",
  },
  {
    id: "console",
    name: "终端",
    hint: "整页等宽字体、紧凑间距，看配置和日志时最顺",
  },
  {
    id: "brief",
    name: "简报",
    hint: "密度最高：行距 1.55、一行 52 字，标题和正文只差一点，用来扫",
  },
];

export const DEFAULT_PALETTE = "pool";
export const DEFAULT_STYLE = "manuscript";

/**
 * 自己调的那一套：挑一个配色当底子，再盖掉其中几个值。
 *
 * 只存「改过的那几个」而不是整套：底子那套以后调了色，自定义里没动过的部分跟着一起更新。
 */
export type Custom = {
  base: string;
  patch: Partial<Record<TokenName, string>>;
};

/** 能被自定义盖掉的变量名（就是 CSS 变量名去掉 `--`）。 */
export type TokenName =
  | "background"
  | "foreground"
  | "card"
  | "border"
  | "muted"
  | "muted-foreground"
  | "accent"
  | "warning"
  | "destructive"
  | "syn-comment"
  | "syn-keyword"
  | "syn-string"
  | "syn-number"
  | "syn-function"
  | "syn-type";

export const DEFAULT_CUSTOM: Custom = { base: DEFAULT_PALETTE, patch: {} };

/** 选中的配色。`"custom"` 表示用上面那套自己调的。 */
export type Selection = string;

export type Applied = {
  palette: Palette;
  style: Style;
  /** 选中的是不是自定义那套。 */
  custom: boolean;
};

function paletteOf(id: string): Palette {
  return PALETTES.find((p) => p.id === id) ?? PALETTES[4]!;
}

function styleOf(id: string): Style {
  return STYLES.find((s) => s.id === id) ?? STYLES[0]!;
}

/**
 * 把配色和风格写到页面上。
 *
 * 写的是 `<html>` 的行内变量和几个 data 属性——行内优先级高于任何样式表，所以它盖得住
 * 公共层 `palette.css` 的默认值，而那份公共文件一个字节都不用改（`browse` 扩展照旧用它）。
 *
 * 颜色本身不在这里算：这里只给 `--lfv-h` / `--lfv-c` 两个数，剩下十几个颜色由
 * `viewer.css` 的 `oklch()` 公式推出来。少算一遍就少一处对不上的可能。
 */
export function applyTheme(
  doc: Document,
  selection: Selection,
  styleId: string = DEFAULT_STYLE,
  custom: Custom = DEFAULT_CUSTOM,
): Applied {
  const isCustom = selection === "custom";
  const palette = paletteOf(isCustom ? custom.base : selection);
  const style = styleOf(styleId);
  const root = doc.documentElement;

  root.style.setProperty("--lfv-h", String(palette.h));
  root.style.setProperty("--lfv-c", String(palette.c));
  root.dataset["lfvPalette"] = isCustom ? custom.base : palette.id;
  root.dataset["lfvPolarity"] = palette.polarity;
  root.dataset["lfvStyle"] = style.id;
  root.style.colorScheme = palette.polarity;

  for (const name of TOKEN_NAMES) root.style.removeProperty(`--${name}`);
  for (const [name, value] of Object.entries(palette.tokens ?? {})) {
    root.style.setProperty(`--${name}`, value);
  }
  // 自定义改过的 token 覆盖底色板；未修改项继续沿用底板原色或公式。
  if (isCustom) {
    for (const [name, value] of Object.entries(custom.patch)) {
      if (value !== undefined) root.style.setProperty(`--${name}`, value);
    }
  }
  return { palette, style, custom: isCustom };
}

export const TOKEN_NAMES: readonly TokenName[] = [
  "background",
  "foreground",
  "card",
  "border",
  "muted",
  "muted-foreground",
  "accent",
  "warning",
  "destructive",
  "syn-comment",
  "syn-keyword",
  "syn-string",
  "syn-number",
  "syn-function",
  "syn-type",
];

/** 设置页里可调的项，顺序即显示顺序。 */
export const FIELDS: readonly { key: TokenName; label: string }[] = [
  { key: "background", label: "页面底色" },
  { key: "foreground", label: "正文颜色" },
  { key: "card", label: "代码块底色" },
  { key: "border", label: "分隔线" },
  { key: "muted-foreground", label: "次要文字" },
  { key: "accent", label: "强调色（链接、引用）" },
  { key: "warning", label: "提示色" },
  { key: "destructive", label: "警示色" },
  { key: "syn-comment", label: "代码 · 注释" },
  { key: "syn-keyword", label: "代码 · 关键字" },
  { key: "syn-string", label: "代码 · 字符串" },
  { key: "syn-number", label: "代码 · 数字" },
  { key: "syn-function", label: "代码 · 函数名" },
  { key: "syn-type", label: "代码 · 类型名" },
];

/**
 * 把页面上算出来的颜色读回成 `#rrggbb`。
 *
 * 走 canvas 而不是读 `getComputedStyle().color`：新版 Chrome 对 `oklch()` 会原样回
 * `oklch(…)` 或 `color(srgb …)`，用正则抠数字会抠出一串小数当成 RGB，色号和对比度全错
 * （2026-09-19 做设计稿时真栽过一次）。canvas 的 `fillStyle` 是真的光栅化，拿到的就是
 * 屏幕上那三个字节。取不到画布（比如 jsdom 里没实现）就原样返回，不假装算得出。
 */
export function hexOf(doc: Document, value: string): string {
  const probe = doc.createElement("span");
  probe.style.color = value;
  doc.body.append(probe);
  const resolved = doc.defaultView?.getComputedStyle(probe).color ?? value;
  probe.remove();
  if (/^#[0-9a-f]{6}$/i.test(resolved)) return resolved.toLowerCase();

  const context = doc.createElement("canvas").getContext?.("2d");
  if (!context) return rgbHex(resolved) ?? resolved;
  context.fillStyle = resolved;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
  return "#" + [r, g, b].map((n) => (n ?? 0).toString(16).padStart(2, "0")).join("");
}

/** `rgb(1, 2, 3)` → `#010203`。只认这一种老式写法，别的返回 null 交给上面兜底。 */
function rgbHex(value: string): string | null {
  const match = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(value);
  if (!match) return null;
  return "#" + match.slice(1, 4).map((n) => Number(n).toString(16).padStart(2, "0")).join("");
}

/** WCAG 的相对亮度。入参是 `#rrggbb`。 */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const linear = channels.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

/**
 * 两个颜色的对比度，`21` 是黑白。正文对底色的下限是 `4.5`（WCAG AA，
 * <https://www.w3.org/TR/WCAG21/#contrast-minimum>）——设置页把实测值写在明处，
 * 自己调颜色调到看不清时当场就能看见。
 */
export function contrast(hexA: string, hexB: string): number {
  const [hi, lo] = [luminance(hexA), luminance(hexB)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}
