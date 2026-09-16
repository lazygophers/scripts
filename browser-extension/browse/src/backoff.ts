/**
 * Reconnect schedule, spec 3.4: exponential backoff with jitter, 500ms -> 60s,
 * then a long cooldown once the daemon has clearly been down for a while.
 * Ported from hangwin/mcp-chrome:entrypoints/background/native-host.ts:14-120.
 */

export const BASE_DELAY_MS = 500;
export const MAX_DELAY_MS = 60_000;
export const COOLDOWN_AFTER_ATTEMPTS = 10;
export const COOLDOWN_MS = 5 * 60_000;

/** Full jitter over the exponential window; `rand` is injected so it is testable. */
export function nextDelay(attempt: number, rand: () => number = Math.random): number {
  if (attempt >= COOLDOWN_AFTER_ATTEMPTS) {
    return COOLDOWN_MS;
  }
  const window = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
  // Half the window is fixed so we never busy-loop at ~0ms.
  return Math.round(window / 2 + (window / 2) * rand());
}
