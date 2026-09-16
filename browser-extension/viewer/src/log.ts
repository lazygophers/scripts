/**
 * 日志视图。单独一个 bundle，打开 `.log` 才加载。
 *
 * 时间戳、级别、消息各占一列，对齐由 CSS grid 管（容器 `grid`、每行 `display: contents`），
 * 所以消息再长也不会把时间那一列挤歪。过滤开关只切容器上的类名，隐藏哪几级也是 CSS 说了算。
 */

/** 认得的级别。写法上大小写不论，`WARNING` 归到 `warn`。 */
const LEVELS = ["fatal", "error", "warn", "info", "debug", "trace"] as const;

export type Level = (typeof LEVELS)[number];

/** 级别词：整词匹配，免得把消息里的 "information" 当成 info。 */
const LEVEL_WORD = /\b(fatal|error|warn(?:ing)?|info|debug|trace)\b/i;

/**
 * 行首的时间戳。两种最常见的写法：ISO 8601（`2026-09-16T03:04:05.123Z`）和
 * 空格分隔的 `2026-09-16 03:04:05`，两者都允许用方括号包起来。
 */
const TIME = /^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?\s*/;

/** 级别只在行首那一小段里找：再往后就是消息正文，不该影响着色。 */
const LEVEL_SCAN = 40;

/** 一行日志拆开之后的样子。认不出级别时 `level` 是 `null`，这种行永远显示。 */
export type Line = {
  time: string;
  level: Level | null;
  message: string;
  json: unknown | undefined;
};

function normalize(word: string): Level {
  const lower = word.toLowerCase();
  return (lower === "warning" ? "warn" : lower) as Level;
}

/** 整行就是一个 JSON 对象的日志：级别和时间从字段里取，常见的几个字段名都认。 */
function fromJson(value: Record<string, unknown>, raw: string): Line {
  const pick = (keys: string[]) => {
    const key = keys.find((k) => typeof value[k] === "string" || typeof value[k] === "number");
    return key === undefined ? "" : String(value[key]);
  };
  const level = LEVEL_WORD.exec(pick(["level", "severity", "lvl"]))?.[1];
  return {
    time: pick(["time", "timestamp", "ts", "@timestamp"]),
    level: level === undefined ? null : normalize(level),
    message: raw,
    json: value,
  };
}

/** 拆一行：先看是不是整行 JSON，再按「时间戳 + 级别词 + 正文」拆。 */
export function parseLine(raw: string): Line {
  const text = raw.trim();
  if (text.startsWith("{")) {
    try {
      const value: unknown = JSON.parse(text);
      if (value !== null && typeof value === "object") {
        return fromJson(value as Record<string, unknown>, raw);
      }
    } catch {
      // 不是 JSON 就按普通日志行接着拆。
    }
  }

  const time = TIME.exec(raw);
  const rest = time === null ? raw : raw.slice(time[0].length);
  const word = LEVEL_WORD.exec(rest.slice(0, LEVEL_SCAN));
  return {
    time: time?.[1] ?? "",
    level: word === undefined || word === null ? null : normalize(word[1] ?? ""),
    message: rest,
    json: undefined,
  };
}

/** 顶部那排开关。原生 checkbox，切的是容器上的 `lfv-hide-<级别>` 类。 */
function filters(doc: Document, view: HTMLElement, present: Set<Level>): HTMLElement {
  const bar = doc.createElement("div");
  bar.className = "lfv-log-filters";
  for (const level of LEVELS) {
    if (!present.has(level)) continue;
    const label = doc.createElement("label");
    label.className = `lfv-log-filter lfv-log-${level}`;
    const box = doc.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    box.dataset["level"] = level;
    box.addEventListener("change", () => {
      view.classList.toggle(`lfv-hide-${level}`, !box.checked);
    });
    label.append(box, level);
    bar.append(label);
  }
  return bar;
}

function row(doc: Document, line: Line, tree: HTMLElement | null): HTMLElement {
  const item = doc.createElement("div");
  item.className = line.level === null ? "lfv-log-row" : `lfv-log-row lfv-log-${line.level}`;

  const time = doc.createElement("span");
  time.className = "lfv-log-time";
  time.textContent = line.time;

  const level = doc.createElement("span");
  level.className = "lfv-log-level";
  level.textContent = line.level ?? "";

  const message = doc.createElement(tree === null ? "span" : "details");
  message.className = "lfv-log-message";
  if (tree === null) {
    message.textContent = line.message;
  } else {
    const head = doc.createElement("summary");
    head.textContent = line.message.trim();
    message.append(head, tree);
  }

  item.append(time, level, message);
  return item;
}

/**
 * 整个日志视图。整行是 JSON 的那些行用折叠树展开，树是向 data 包借的——
 * 文件里一行 JSON 都没有时，那个包一次都不加载。
 */
export async function renderLog(doc: Document, text: string): Promise<HTMLElement> {
  const view = doc.createElement("div");
  view.className = "lfv-log";

  const lines = (text === "" ? [] : text.replace(/\n$/, "").split("\n")).map(parseLine);
  const hasJson = lines.some((line) => line.json !== undefined);
  const renderTree = hasJson
    ? ((await import(chrome.runtime.getURL("data.js"))).renderTree as (
        d: Document,
        v: unknown,
      ) => HTMLElement)
    : null;

  const present = new Set(lines.flatMap((line) => (line.level === null ? [] : [line.level])));
  const rows = lines.map((line) =>
    row(doc, line, renderTree === null || line.json === undefined ? null : renderTree(doc, line.json)),
  );

  const host = doc.createElement("div");
  host.className = "lfv-log-view";
  view.append(...rows);
  host.append(filters(doc, view, present), view);
  return host;
}
