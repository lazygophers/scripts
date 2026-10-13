/**
 * Wire envelope, spec 3.3. Shape is WebDriver BiDi's; private capabilities
 * carry a `lg:` prefix (BiDi 3.3 reserves colon-prefixed module names).
 */

export interface Command {
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

export interface Success {
  type: "success";
  id: number;
  result: unknown;
}

export interface ErrorReply {
  type: "error";
  id: number;
  error: ErrorCode;
  message: string;
}

export interface Event {
  type: "event";
  method: string;
  params: Record<string, unknown>;
}

export type Outbound = Success | ErrorReply | Event;

/**
 * BiDi standard error codes, the subset this extension can produce, plus the
 * two private ones. A colon-prefixed code is a BiDi extension namespace (§3.3,
 * same rule as the `lg:` command modules), so the daemon's validator accepts
 * the 8 standard codes plus anything containing a colon —
 * `lib/browse_protocol.py` implements exactly that.
 *
 * `lg:browser not connected` is only ever produced daemon-side; it is listed
 * here so both ends carry the same enum.
 */
export type ErrorCode =
  | "invalid argument"
  | "timeout"
  | "no such element"
  | "no such frame"
  | "no such script"
  | "unknown command"
  | "unknown error"
  | "unsupported operation"
  | "lg:browser not connected"
  | "lg:user rejected"
  | "lg:feature disabled";

/** Thrown by handlers to pick the error code instead of `unknown error`. */
export class CommandError extends Error {
  // Assigned in the body, not as a constructor parameter property: Node's
  // type-stripping (`node --test` on .ts sources) rejects those.
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = "CommandError";
    this.code = code;
  }
}

/**
 * Argument checks. Every handler needs the same three shapes, so they live here
 * next to `CommandError` instead of being retyped at each call site.
 *
 * `hint` is appended to the message when the plain name is not enough to act on
 * (`key must be a non-empty string, e.g. "Enter"`).
 */
export function asString(value: unknown, name: string, hint = ""): string {
  if (typeof value !== "string") {
    throw new CommandError("invalid argument", `${name} must be a string${hint}`);
  }
  return value;
}

/** Same, but empty is not a usable id, url or key either. */
export function requireString(value: unknown, name: string, hint = ""): string {
  if (typeof value !== "string" || value === "") {
    throw new CommandError("invalid argument", `${name} must be a non-empty string${hint}`);
  }
  return value;
}

/** `undefined` passes through untouched; anything else must be a string. */
export function optionalString(value: unknown, name: string, hint = ""): string | undefined {
  return value === undefined ? undefined : asString(value, name, hint);
}

export function isCommand(value: unknown): value is Command {
  const c = value as Command | null;
  return (
    typeof c === "object" &&
    c !== null &&
    typeof c.id === "number" &&
    typeof c.method === "string"
  );
}

/**
 * Arc 的 MV3 service worker 里 tabs/tabGroups 的 query promise 永不 settle
 * （实测 2026-10-13：tabs.create、chrome.system.cpu 正常，query 全部挂起，
 * 指令无回包直到 CLI 30s 超时）。race 一个兜底超时把挂死变明确报错；
 * 正常浏览器 query 毫秒级返回，10s 绰绰有余。
 */
export const QUERY_TIMEOUT_MS = 10_000;

/**
 * 归属降级的开关（Arc 无组降级，2026-10-13）：query 挂死/失败的环境里，
 * 组员身份判不了，调用方退回登记表匹配。探针一次、缓存整个 SW 生命周期
 * ——降级路径本身是 fail closed（登记表没有就拒），误判最多退回登记表语义。
 */
let hangProbe: Promise<boolean> | undefined;

export function queriesHang(): Promise<boolean> {
  hangProbe ??= (async () => {
    // API 缺失不算挂死：让正常路径的 requireApi / 原生报错去说话。
    if (typeof chrome.tabs?.query !== "function") {
      return false;
    }
    try {
      await querySettle(chrome.tabs.query({}), "chrome.tabs.query (capability probe)", 3_000);
      return false;
    } catch {
      return true;
    }
  })();
  return hangProbe;
}

/** 测试钩子：清探针缓存。 */
export function resetQueryProbe(): void {
  hangProbe = undefined;
}

/** 测试钩子：预置探针结果，降级用例不必等真超时。 */
export function seedQueryProbe(hangs: boolean): void {
  hangProbe = Promise.resolve(hangs);
}

export function querySettle<T>(p: Promise<T>, what: string, ms = QUERY_TIMEOUT_MS): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new CommandError(
      "timeout",
      `${what} did not settle within ${ms}ms; Arc's tabs/tabGroups query is known to hang`,
    )), ms);
  });
  // 超时后原 promise 若再落败，不能变成 unhandled rejection。
  p.catch(() => undefined);
  return Promise.race([p, timeout]).finally(() => {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  });
}
