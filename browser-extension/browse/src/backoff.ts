/**
 * Reconnect schedule, spec 3.4: exponential backoff with jitter, 500ms -> 60s,
 * then a long cooldown once the daemon has clearly been down for a while.
 * Ported from hangwin/mcp-chrome:entrypoints/background/native-host.ts:14-120.
 */

export const BASE_DELAY_MS = 500;
export const MAX_DELAY_MS = 60_000;
export const COOLDOWN_AFTER_ATTEMPTS = 10;
// bridge 由系统服务常驻（browse install 一并装），重启窗口是分钟级而不是「再也不来」。
// 5 分钟冷却（mcp-chrome 的原生宿主场景）会让 bridge 已经活了、扩展还在睡，
// 用户这头就是「浏览器未连接」—— 降到 60 秒，最坏一分钟内重连。
export const COOLDOWN_MS = 60_000;

/** Full jitter over the exponential window; `rand` is injected so it is testable. */
export function nextDelay(attempt: number, rand: () => number = Math.random): number {
  if (attempt >= COOLDOWN_AFTER_ATTEMPTS) {
    return COOLDOWN_MS;
  }
  const window = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
  // Half the window is fixed so we never busy-loop at ~0ms.
  return Math.round(window / 2 + (window / 2) * rand());
}
