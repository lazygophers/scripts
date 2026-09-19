/**
 * 主题 = **配色 × 风格** 两层（2026-09-19 定稿，第二轮设计）。
 *
 * 第一轮把主题做成「一套颜色 + 一套排版」捆在一起，用户否掉了全部六项。第二轮重做，
 * 拆成互相独立的两层：
 *
 * - **配色（palette）**：八套。底色不是灰，是「朝某个色相偏一点点的中性」——这条抄
 *   Solarized（<https://ethanschoonover.com/solarized/>，用 CIELAB 算出来的十六色），
 *   它只做过一次色相选择，我们做八次。**明度阶梯八套锁死**，只让色相转：换配色换心情，
 *   不换可读性。阶梯的档位数照 Radix 的语义色阶（<https://www.radix-ui.com/colors>）。
 *   所以每套配色在这里只有两个数：色相角 `h` 和彩度系数 `c`。真正的颜色由 CSS 里那条
 *   `oklch()` 公式算出来（`viewer.css` 的「明度阶梯」一节）。
 *   墨水屏就是 `c: 0` 的那两套——纯灰阶不是另写一张表，是同一条公式的边界值。
 *
 * - **风格（style）**：四套。它换的不是颜色，是**版面本身**：字体、字号、行距、每行字数、
 *   标题的大小关系、有没有规则线、圆角有没有、代码块怎么摆。用户原话：「主题不是单纯
 *   颜色的变化」。风格的实现全在 CSS（`html[data-lfv-style="…"]` 那几段），这里只记
 *   名字和说明。
 *
 * 八套配色 × 四种风格 = 32 种成品，要维护的只有 8 个 (h, c) 对 + 4 段 CSS。
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
};

/** 亮面四套在前、暗面四套在后；这个顺序就是菜单和设置页里的顺序。 */
export const PALETTES: readonly Palette[] = [
  { id: "brass", name: "黄铜", hint: "暖纸调，白天读长文最耐看", polarity: "light", h: 85, c: 1 },
  { id: "ochre", name: "赭石", hint: "陶土色，亮面里带彩最重的一套", polarity: "light", h: 40, c: 1.15 },
  { id: "mauve", name: "藕合", hint: "冷粉灰，亮面里唯一的冷调", polarity: "light", h: 320, c: 0.9 },
  { id: "eink", name: "墨水屏·亮", hint: "纯灰阶、对比最高，语法靠字形分", polarity: "light", h: 0, c: 0 },
  { id: "pool", name: "深潭", hint: "青黑，暗面的基准色", polarity: "dark", h: 200, c: 1 },
  { id: "soot", name: "松烟", hint: "墨锭那种绿黑，比青黑暖", polarity: "dark", h: 155, c: 1 },
  { id: "night", name: "夜航", hint: "紫黑，暗面里最冷的一套", polarity: "dark", h: 275, c: 1.1 },
  { id: "einkd", name: "墨水屏·暗", hint: "纯黑底白字，暗面对比最高", polarity: "dark", h: 0, c: 0 },
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

  // 自定义改过的那几个值直接盖在最上面；没改过的仍由公式算，底子调色时跟着更新。
  for (const name of TOKEN_NAMES) {
    const value = isCustom ? custom.patch[name] : undefined;
    if (value === undefined) root.style.removeProperty(`--${name}`);
    else root.style.setProperty(`--${name}`, value);
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
