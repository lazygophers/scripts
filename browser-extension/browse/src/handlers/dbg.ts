/**
 * Shared `chrome.debugger` attach manager: one attach per tab, reference-counted.
 *
 * Chrome allows a single debugger attach per target. Console collection (Runtime
 * domain) and net routes (Fetch domain) can both want the debugger on the same
 * tab; whoever attaches second would fail with "already attached". Both go
 * through this module instead, and the tab detaches only when the last user
 * releases it.
 *
 * Forced detach (user opened DevTools, tab crashed) is reported through
 * `chrome.debugger.onDetach`; each caller drops its own state there and calls
 * `forgetDebugger` so the next `acquireDebugger` re-attaches instead of
 * trusting a stale refcount.
 */
const refcounts = new Map<number, number>();

export async function acquireDebugger(tabId: number): Promise<void> {
  const held = refcounts.get(tabId) ?? 0;
  if (held === 0) {
    await chrome.debugger.attach({ tabId }, "1.3");
  }
  refcounts.set(tabId, held + 1);
}

export async function releaseDebugger(tabId: number): Promise<void> {
  const held = refcounts.get(tabId) ?? 0;
  if (held > 1) {
    refcounts.set(tabId, held - 1);
    return;
  }
  refcounts.delete(tabId);
  if (held === 1) {
    await chrome.debugger.detach({ tabId });
  }
}

/** External detach: drop the count so the next acquire re-attaches. */
export function forgetDebugger(tabId: number): void {
  refcounts.delete(tabId);
}
