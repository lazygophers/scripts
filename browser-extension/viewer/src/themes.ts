/**
 * 主题：按「在什么介质上读」分，不是按配色方案分（2026-09-18 设计定稿，方向 C「纸与墨」）。
 *
 * 所以每套主题换的不只是颜色——字体、字号、行距、每行多少字一起换。「纸」是宋体、
 * 行距 1.9、每行 68 字；「夜」是无衬线、行距 1.8、每行 74 字。这是阅读器的思路，
 * 不是编辑器的思路。
 *
 * 实现上只有一件事：往 `<html>` 上写一组 CSS 变量。`viewer.css` 里的 90 处
 * `var(--…)` 因此全部跟着变，界面代码一行都不用动。写成行内变量是故意的——它盖得住
 * 公共层 `palette.css` 的默认值，而 `palette.css` 本身不动，`browse` 扩展照旧
 * （用户 2026-09-18 选定「只给 viewer」）。
 */

/** 一套主题的全部可调值。键名就是 CSS 变量名去掉 `--`。 */
export type Tokens = {
  background: string;
  foreground: string;
  card: string;
  border: string;
  muted: string;
  "muted-foreground": string;
  /** 强调色：链接、引用条、代码里的关键字和字符串都用它。 */
  accent: string;
  /** 提示色：markdown 里「注意 / 警告」那种提示框的左边色条。 */
  warning: string;
  /** 出错、删除行这类要扎眼的地方。 */
  destructive: string;
  /** 正文字体。代码块不受它影响，那边永远是等宽字体。 */
  "lfv-font": string;
  /** 正文字号，带单位。 */
  "lfv-size": string;
  /** 正文行距，无单位的倍数。 */
  "lfv-leading": string;
  /** 正文一行多少字（`ch` 是「一个 0 的宽度」，中文里约等于半个字）。 */
  "lfv-measure": string;
};

export type ThemeId = "paper" | "night" | "eink" | "moss";

/** 浏览器要知道这套主题是亮是暗，滚动条和表单控件才跟着对。 */
export type Scheme = "light" | "dark";

export type Theme = {
  id: ThemeId;
  /** 给人看的名字。 */
  name: string;
  /** 一句话说它适合什么场景，设置页里显示。 */
  hint: string;
  scheme: Scheme;
  tokens: Tokens;
};

/**
 * 四种介质。顺序就是设置页和切换菜单里的顺序。
 *
 * 颜色值写成十六进制而不是 `oklch()`：自定义主题那一档要把这些值塞进
 * `<input type="color">`，而那个控件只认 `#rrggbb`。
 */
export const THEMES: readonly Theme[] = [
  {
    id: "paper",
    name: "纸",
    hint: "暖白底 + 宋体，接近纸质书，白天读长文最舒服",
    scheme: "light",
    tokens: {
      background: "#faf8f4",
      foreground: "#2b2723",
      card: "#f2efe8",
      border: "#e2ddd2",
      muted: "#efece4",
      "muted-foreground": "#8a8378",
      accent: "#9a4c2e",
      warning: "#b07d2b",
      destructive: "#a33a2a",
      "lfv-font": '"Songti SC", "Source Han Serif SC", Georgia, serif',
      "lfv-size": "16.5px",
      "lfv-leading": "1.9",
      "lfv-measure": "68ch",
    },
  },
  {
    id: "night",
    name: "夜",
    hint: "深墨底，暗处不刺眼，默认就是它",
    scheme: "dark",
    tokens: {
      background: "#16171a",
      foreground: "#dcd9d3",
      card: "#1e2024",
      border: "#2e3136",
      muted: "#23262b",
      "muted-foreground": "#8b8a86",
      accent: "#c9a227",
      warning: "#d98c3a",
      destructive: "#c9564b",
      "lfv-font": '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
      "lfv-size": "15.5px",
      "lfv-leading": "1.8",
      "lfv-measure": "74ch",
    },
  },
  {
    id: "eink",
    name: "墨水屏",
    hint: "纯灰阶、高对比、字更大，像电子书阅读器",
    scheme: "light",
    tokens: {
      background: "#ffffff",
      foreground: "#111111",
      card: "#f2f2f2",
      border: "#cfcfcf",
      muted: "#ebebeb",
      "muted-foreground": "#5a5a5a",
      accent: "#111111",
      warning: "#555555",
      destructive: "#3d3d3d",
      "lfv-font": '"PingFang SC", -apple-system, BlinkMacSystemFont, sans-serif',
      "lfv-size": "17.5px",
      "lfv-leading": "1.95",
      "lfv-measure": "62ch",
    },
  },
  {
    id: "moss",
    name: "苔",
    hint: "低饱和绿灰，对比温和，久看不累",
    scheme: "light",
    tokens: {
      background: "#e9ede7",
      foreground: "#262c25",
      card: "#dfe5dc",
      border: "#ccd4c8",
      muted: "#d9e0d6",
      "muted-foreground": "#6d766a",
      accent: "#3f6b4a",
      warning: "#8a6a2f",
      destructive: "#8a4b3c",
      "lfv-font": '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
      "lfv-size": "16px",
      "lfv-leading": "1.85",
      "lfv-measure": "70ch",
    },
  },
];

/** 没选过主题时用哪一套。 */
export const DEFAULT_THEME: ThemeId = "night";

/**
 * 自定义主题：挑一套现成的当底子，再盖掉其中几个值。
 *
 * 只存「改过的那几个」而不是整套：底子那套以后调了颜色，自定义主题里没动过的部分
 * 跟着一起更新，不会停在旧值上。
 */
export type Custom = {
  base: ThemeId;
  patch: Partial<Tokens>;
};

export const DEFAULT_CUSTOM: Custom = { base: DEFAULT_THEME, patch: {} };

/** 选中的是哪一套。`"custom"` 是那套自己调的。 */
export type Selection = ThemeId | "custom";

function themeOf(id: ThemeId): Theme {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[1]!;
}

/** 算出最终要用的那套值：选了自定义就是「底子 + 改动」，否则就是那套本身。 */
export function resolve(selection: Selection, custom: Custom = DEFAULT_CUSTOM): Theme {
  if (selection !== "custom") return themeOf(selection);
  const base = themeOf(custom.base);
  return {
    ...base,
    id: base.id,
    name: "自定义",
    hint: `以「${base.name}」为底子改的`,
    // 亮暗跟着底子走：自定义只改颜色值，不改「这是亮主题还是暗主题」这件事。
    tokens: { ...base.tokens, ...custom.patch },
  };
}

/**
 * 把一套主题写到页面上。
 *
 * 变量写在 `<html>` 的行内样式里——行内的优先级高于任何样式表，所以它盖得住
 * `palette.css` 的默认值，而那份公共文件一个字节都不用改。
 */
export function applyTheme(doc: Document, selection: Selection, custom?: Custom): Theme {
  const theme = resolve(selection, custom);
  const root = doc.documentElement;
  for (const [name, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(`--${name}`, value);
  }
  root.style.colorScheme = theme.scheme;
  root.dataset["lfvTheme"] = selection;
  return theme;
}

/** 用户能在自定义里调的那几个颜色，顺序即设置页里的顺序。 */
export const COLOR_FIELDS: readonly { key: keyof Tokens; label: string }[] = [
  { key: "background", label: "页面底色" },
  { key: "foreground", label: "正文颜色" },
  { key: "card", label: "代码块底色" },
  { key: "border", label: "分隔线" },
  { key: "muted-foreground", label: "次要文字（注释、说明）" },
  { key: "accent", label: "强调色（链接、关键字）" },
  { key: "warning", label: "提示色（注意、警告条）" },
  { key: "destructive", label: "警示色（报错、删除行）" },
];

/** 自定义里能调的排版项。`step` 给滑块用。 */
export const TYPE_FIELDS: readonly {
  key: keyof Tokens;
  label: string;
  min: number;
  max: number;
  step: number;
  unit: string;
}[] = [
  { key: "lfv-size", label: "正文字号", min: 12, max: 22, step: 0.5, unit: "px" },
  { key: "lfv-leading", label: "行距", min: 1.4, max: 2.4, step: 0.05, unit: "" },
  { key: "lfv-measure", label: "每行字数", min: 48, max: 100, step: 1, unit: "ch" },
];

/** `"16.5px"` → `16.5`。取不出数字就回退到底子的值。 */
export function numberOf(value: string, fallback: number): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
