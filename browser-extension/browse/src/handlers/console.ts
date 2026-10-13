import { clean } from "../redact.ts";
import { CommandError } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi, resolveContextOnce, targetUrl, type Target } from "./context.ts";
import { acquireDebugger, forgetDebugger, releaseDebugger } from "./dbg.ts";
import { isOwnTab } from "./ownership.ts";

/**
 * 网页控制台采集（2026-10-13）：`lg:console.start/read/stop`。
 *
 * chrome.debugger 的 Runtime 域是唯一能拿到页面 console 输出和未捕获异常的
 * 路径（webRequest 只有网络，scripting 只能主动求值）。代价是 debugger 权限
 * 本身很广：**绝不全局自动 attach**，只有用户显式 `lg:console.start` 的那一
 * 个 owned tab 才 attach，`stop` / tab 关闭 / detach / bridge 断开都会摘掉。
 *
 * 历史不可回溯：start 之前的输出拿不到（CDP 只推事件不回放）。
 *
 * 安全口径：
 * - RemoteObject 只读 `value` / `description` / `unserializableValue` 这些
 *   CDP 序列化好的字段，**绝不碰对象 getter**（求值页面 JS 等于任意代码执行）。
 * - 文本过 `clean()` 再入环（先脱敏后截断），但自由文本无法保证完全脱敏。
 * - 每条事件入环前重查所有权：tab 被拖出组就丢弃事件并 detach。
 */

/** 每 tab 环形缓冲上限。 */
const RING = 500;
/** 同时采集的 tab 数上限（debugger attach 是重资源）。 */
const MAX_TABS = 10;

export type ConsoleLevel = "log" | "info" | "warning" | "error" | "debug";

export interface ConsoleEntry {
  /** CDP 的 timestamp（epoch 毫秒）。 */
  timestamp: number;
  level: ConsoleLevel;
  text: string;
  /** 未捕获异常的调用栈（仅 exception 事件有）。 */
  stack?: string;
}

interface Session {
  tabId: number;
  /** 竞态键：清环 / 重挂都递增；await 之后对不上就丢弃这条事件。 */
  generation: number;
  entries: ConsoleEntry[];
  dropped: number;
}

const sessions = new Map<number, Session>();

// ---------------------------------------------------------------- 事件监听

/**
 * CDP 事件的最小结构（只声明用到的字段；多余字段照吃不管）。
 * RemoteObject 的字段都是 CDP 序列化产物，直接读不存在 getter 执行。
 */
interface RemoteObject {
  type?: string;
  subtype?: string;
  value?: unknown;
  description?: string;
  unserializableValue?: string;
  className?: string;
}

interface ConsoleEvent {
  type?: string;
  timestamp?: number;
  args?: RemoteObject[];
}

interface CallFrame {
  functionName?: string;
  url?: string;
  lineNumber?: number;
  columnNumber?: number;
}

interface ExceptionEvent {
  timestamp?: number;
  exceptionDetails?: {
    text?: string;
    exception?: RemoteObject;
    stackTrace?: { callFrames?: CallFrame[] };
  };
}

/** RemoteObject → 文本。不执行任何页面对象的 getter（见文件头安全口径）。 */
function renderArg(arg: RemoteObject): string {
  if (typeof arg.value === "string") {
    return arg.value;
  }
  if (arg.unserializableValue !== undefined) {
    return arg.unserializableValue;
  }
  if (arg.value !== undefined && arg.value !== null) {
    return String(arg.value);
  }
  // 对象/函数：用 CDP 自己给的 description（"TypeError: x" / "{a: 1}"），
  // 没有 description 用类型名。绝不求值。
  return arg.description ?? arg.className ?? arg.type ?? "object";
}

function stackOf(frames: CallFrame[] | undefined): string | undefined {
  if (!frames || frames.length === 0) {
    return undefined;
  }
  // CDP 的行列号是 0 基，展示时 +1 对齐 DevTools 的显示
  return frames
    .map((f) =>
      `${f.functionName || "<anonymous>"}@${f.url ?? ""}:${(f.lineNumber ?? 0) + 1}:${(f.columnNumber ?? 0) + 1}`)
    .join("\n");
}

/** append 的收尾：脱敏、截断、环形淘汰。同步——所有 await 都在它之前。 */
function append(session: Session, entry: ConsoleEntry): void {
  const safe = clean(entry) as ConsoleEntry;
  session.entries.push(safe);
  if (session.entries.length > RING) {
    session.dropped += session.entries.length - RING;
    session.entries.splice(0, session.entries.length - RING);
  }
}

const onEvent = (
  source: { tabId?: number },
  method: string,
  params: object | undefined,
): void => {
  if (source.tabId === undefined) {
    return;
  }
  const session = sessions.get(source.tabId);
  if (session === undefined) {
    return;
  }
  if (method !== "Runtime.consoleAPICalled" && method !== "Runtime.exceptionThrown") {
    return;
  }
  const generation = session.generation;
  // 所有权重查是异步的：await 回来之后 session 可能已被 stop / 撤权摘掉，
  // 或 generation 已翻页（导航清环）。对不上就丢，宁丢不串。
  void (async () => {
    let owned = false;
    try {
      const tab = await chrome.tabs.get(source.tabId!);
      owned = await isOwnTab(tab);
    } catch {
      owned = false;
    }
    if (sessions.get(source.tabId!) !== session || session.generation !== generation) {
      return;
    }
    if (!owned) {
      // 撤权：丢弃这条，摘掉整个会话
      void forget(source.tabId!).catch(() => {});
      return;
    }
    if (method === "Runtime.consoleAPICalled") {
      const ev = (params ?? {}) as ConsoleEvent;
      const level = (
        ["log", "info", "warning", "error", "debug"] as const
      ).includes(ev.type as ConsoleLevel)
        ? (ev.type as ConsoleLevel)
        : "log";
      append(session, {
        timestamp: ev.timestamp ?? Date.now(),
        level,
        text: (ev.args ?? []).map(renderArg).join(" ") || level,
      });
    } else {
      const ev = (params ?? {}) as ExceptionEvent;
      const details = ev.exceptionDetails ?? {};
      append(session, {
        timestamp: ev.timestamp ?? Date.now(),
        level: "error",
        text:
          details.exception?.description
          ?? details.exception?.className
          ?? details.text
          ?? "uncaught exception",
        ...(stackOf(details.stackTrace?.callFrames) !== undefined
          ? { stack: stackOf(details.stackTrace?.callFrames)! }
          : {}),
      });
    }
  })();
};

const onDetach = (source: { tabId?: number }, _reason: string): void => {
  if (source.tabId !== undefined) {
    sessions.delete(source.tabId);
    // 共享管理器（dbg.ts）的计数一并作废，下次 acquire 会重新 attach
    forgetDebugger(source.tabId);
  }
};

let listenersAttached = false;

function attachDebuggerListeners(): void {
  if (listenersAttached) {
    return;
  }
  listenersAttached = true;
  chrome.debugger.onEvent.addListener(onEvent);
  chrome.debugger.onDetach.addListener(onDetach);
}

/** 摘掉一个 tab 的采集（detach + 删会话）。detach 失败原样抛。 */
async function forget(tabId: number): Promise<void> {
  sessions.delete(tabId);
  await releaseDebugger(tabId);
}

// ---------------------------------------------------------------- handlers

async function requireTab(params: Record<string, unknown>): Promise<Target> {
  const target = await resolveContextOnce(params);
  if (target.frameId !== undefined) {
    throw new CommandError(
      "unsupported operation",
      `console collection works on a tab, not a frame; drop the .${target.frameId} suffix`,
    );
  }
  return target;
}

/** `lg:console.start`：对 owned tab attach debugger、开 Runtime 域采集。 */
export async function consoleStart(
  params: Record<string, unknown>,
): Promise<{ context: string; started: boolean }> {
  requireApi("debugger", "reading console output");
  const target = await requireTab(params);
  const url = await targetUrl(target);
  await confirm({ action: "attachDebugger", method: "lg:console.start", url });
  if (sessions.has(target.tabId)) {
    return { context: String(target.tabId), started: true };
  }
  if (sessions.size >= MAX_TABS) {
    throw new CommandError(
      "invalid argument",
      `already collecting ${sessions.size} tabs (max ${MAX_TABS}); stop one first`,
    );
  }
  attachDebuggerListeners();
  try {
    // console 和 net 路由合租同一个 attach（dbg.ts 计数）
    await acquireDebugger(target.tabId);
  } catch (err) {
    throw new CommandError(
      "unsupported operation",
      `cannot attach the debugger to tab ${target.tabId} (another debugger, e.g. DevTools, may be open): ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  try {
    await chrome.debugger.sendCommand({ tabId: target.tabId }, "Runtime.enable");
  } catch (err) {
    // Runtime 域都开不了就别挂着 attach
    await chrome.debugger.detach({ tabId: target.tabId }).catch(() => {});
    throw new CommandError("unknown error", `Runtime.enable failed: ${err instanceof Error ? err.message : String(err)}`);
  }
  sessions.set(target.tabId, {
    tabId: target.tabId,
    generation: 0,
    entries: [],
    dropped: 0,
  });
  return { context: String(target.tabId), started: true };
}

const LEVELS = new Set(["error"]);

/** `lg:console.read`：读采集到的条目。没在采集不算错（轮询友好）。 */
export async function consoleRead(
  params: Record<string, unknown>,
): Promise<{
  context: string;
  collecting: boolean;
  entries: ConsoleEntry[];
  dropped: number;
}> {
  const target = await requireTab(params);
  if (params.limit !== undefined
      && (typeof params.limit !== "number" || !Number.isInteger(params.limit) || params.limit < 1 || params.limit > RING)) {
    throw new CommandError("invalid argument", `limit must be an integer in [1, ${RING}]`);
  }
  const level = params.level;
  if (level !== undefined && (typeof level !== "string" || !LEVELS.has(level))) {
    throw new CommandError("invalid argument", `level must be one of ${[...LEVELS].join(", ")}`);
  }
  const session = sessions.get(target.tabId);
  if (session === undefined) {
    return { context: String(target.tabId), collecting: false, entries: [], dropped: 0 };
  }
  let entries = session.entries;
  if (level !== undefined) {
    entries = entries.filter((e) => e.level === level);
  }
  if (typeof params.limit === "number") {
    entries = entries.slice(-params.limit);
  }
  return {
    context: String(target.tabId),
    collecting: true,
    entries: [...entries],
    dropped: session.dropped,
  };
}

/** `lg:console.stop`：停采集并 detach。没在采集返回 stopped:false，不报错。 */
export async function consoleStop(
  params: Record<string, unknown>,
): Promise<{ context: string; stopped: boolean }> {
  requireApi("debugger", "reading console output");
  const target = await requireTab(params);
  if (!sessions.has(target.tabId)) {
    return { context: String(target.tabId), stopped: false };
  }
  // detach 失败原样抛（不谎报停止），会话留着以便重试
  await forget(target.tabId);
  return { context: String(target.tabId), stopped: true };
}

// ---------------------------------------------------------------- 生命周期

/** tab 关了：摘掉采集（background.ts 的 onRemoved 调）。 */
export function consoleForget(tabId: number): void {
  sessions.delete(tabId);
  forgetDebugger(tabId);
  void chrome.debugger?.detach({ tabId }).catch(() => {});
}

/** 导航/刷新：旧页面的条目作废（清环 + 翻 generation），采集继续。 */
export function consoleNavigate(tabId: number): void {
  const session = sessions.get(tabId);
  if (session !== undefined) {
    session.generation += 1;
    session.entries = [];
    session.dropped = 0;
  }
}

/** bridge 断开：全摘（background.ts 的状态回调调）。 */
export function consoleReset(): void {
  for (const tabId of sessions.keys()) {
    consoleForget(tabId);
  }
}

/** 测试钩子：回到「没采集、没监听」的初始状态。 */
export function resetConsole(): void {
  const tabs = [...sessions.keys()];
  sessions.clear();
  listenersAttached = false;
  // dbg.ts 的引用计数是模块级状态，测试间不清会越积越多（同 resetNetRules）
  for (const tabId of tabs) {
    forgetDebugger(tabId);
  }
}
