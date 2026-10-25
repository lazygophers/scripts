import { clean } from "./redact.ts";

/**
 * 扩展侧遥测：把连接层的关键事件落进统一 JSONL（`$TEMP/lazygophers/scripts.log`，
 * `lib/log.py` 格式，logger="browse-extension"）。断联期不丢：队列存
 * `chrome.storage.local["browse:logq"]`，flush 经注入的 sink 发 type-less
 * command 帧 `lg:extension.log`，bridge 回 success 才清已发条目（ack-gated）；
 * request 无超时，重复落盘接受（对账按 event+t 去重）。
 */

export type TelemetryLevel = "info" | "warning" | "error";

export interface TelemetryEntry {
  event: string;
  level: TelemetryLevel;
  /** 扩展侧时刻（秒，Unix epoch），和 `lib/log.py` 的 at 对账。 */
  t: number;
  fields?: Record<string, unknown>;
}

/** 独立 key，不撞 browse:audit / browse:instanceId / browse:ownership / browse:pagecache。 */
export const QUEUE_KEY = "browse:logq";
export const RING_MAX = 100;
export const FLUSH_DEBOUNCE_MS = 2000;
/** wire 约定：每批 ≤100 条。 */
const BATCH_MAX = 100;

// info 全收会把 SW 每次唤醒都变成日志（alarm 半分钟叫一次），只留点名的事件；
// warning / error 全收。
const INFO_ALLOWLIST = new Set(["ws.connected", "sw-start"]);

type TelemetrySink = (entries: TelemetryEntry[]) => Promise<unknown>;

// 照抄 events.ts 的 setEventSink 模式：native-port 反向 import 会成环，
// sink 由 background.ts 启动时注入（经 NativeConnection.request 发出）。
let sink: TelemetrySink | null = null;

export function setTelemetrySink(fn: TelemetrySink | null): void {
  sink = fn;
}

// 入队/flush 串成一条 promise 链：两次 log() 并发读改写 storage 会互相覆盖丢条目。
let chain: Promise<void> = Promise.resolve();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

async function readQueue(): Promise<TelemetryEntry[]> {
  const got = await chrome.storage.local.get(QUEUE_KEY);
  const q = got[QUEUE_KEY];
  return Array.isArray(q) ? (q as TelemetryEntry[]) : [];
}

/** 记一条。任何异常（storage 缺席、配额满、clean 抛）都吞掉——遥测永远不能反过来把连接层打挂，更不能递归入队。 */
export function log(
  event: string,
  level: TelemetryLevel,
  fields?: Record<string, unknown>,
): void {
  try {
    if (level === "info" && !INFO_ALLOWLIST.has(event)) {
      return;
    }
    const entry: TelemetryEntry = { event, level, t: Date.now() / 1000 };
    const cleaned = clean(fields) as Record<string, unknown> | undefined;
    if (cleaned !== undefined) {
      entry.fields = cleaned;
    }
    chain = chain.then(async () => {
      let q: TelemetryEntry[];
      try {
        q = await readQueue();
      } catch {
        return; // 没(storage|读挂了)：这条只能丢
      }
      q.push(entry);
      if (q.length > RING_MAX) {
        q.splice(0, q.length - RING_MAX); // 满丢最老
      }
      try {
        await chrome.storage.local.set({ [QUEUE_KEY]: q });
      } catch {
        return; // 写挂了：这条丢了，不重试（重试会把坏队列变成无限循环）
      }
    }).catch(() => {});
    scheduleFlush();
  } catch {
    // 连 clean() 都抛了：放弃这条，绝不再调 log()
  }
}

function scheduleFlush(): void {
  if (flushTimer !== null) {
    return;
  }
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flush();
  }, FLUSH_DEBOUNCE_MS);
}

/** 测试用：清掉模块级防抖定时器，用例之间互不串。 */
export function resetForTests(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
}

/**
 * 把队列发给 bridge。只排程两处：log() 入队（2s 防抖）和 socket.onopen（补积压）。
 * sink resolve 才逐批清已发条目；reject（含没 socket 时 request 的立即拒绝）保留重发。
 */
export function flush(): void {
  chain = chain.then(async () => {
    let q: TelemetryEntry[];
    try {
      q = await readQueue();
    } catch {
      return; // storage 坏：读不出就当作没有
    }
    while (q.length > 0 && sink !== null) {
      const batch = q.slice(0, BATCH_MAX);
      try {
        await sink(batch);
      } catch {
        return; // reject：整批留下次重发
      }
      q = q.slice(batch.length);
      try {
        await chrome.storage.local.set({ [QUEUE_KEY]: q });
      } catch {
        return; // 清不掉了：下轮重发会重复落盘，接受（对账按 event+t 去重）
      }
    }
  }).catch(() => {});
}
