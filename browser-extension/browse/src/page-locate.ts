/**
 * Injected with `chrome.scripting.executeScript({ files: [...] })`, which is
 * the only way to get `locateInPage` into a page with its nested helpers
 * intact: the `func:` form serialises the function and drops every closure, and
 * `new Function` is unavailable in an ISOLATED world under MV3's CSP.
 *
 * Injecting it again is harmless (it just reassigns), so callers do it before
 * every page command rather than tracking which frames already have it.
 */
import { locateInPage } from "./locator.ts";

(globalThis as unknown as Record<string, unknown>).__browseLocate = locateInPage;

/**
 * The element-ref registry, also living on the page's `globalThis` because
 * serialised page functions cannot close over module bindings.
 *
 * `snapshot` assigns each visible element a stable ref (`@e1`, `@e2`, …);
 * `ref=@e3` locators resolve through `els`. The registry lives in the ISOLATED
 * world, so a navigation tears it down for free: every ref goes stale at once,
 * which is exactly the semantics we want (refs are page-load-scoped).
 *
 * Re-injecting this file must NOT reset it — that would invalidate every ref on
 * every page command, so `ensureRefRegistry` only initialises when absent.
 */
export interface RefRegistry {
  next: number;
  refs: WeakMap<Element, string>;
  els: Map<string, WeakRef<Element>>;
  /** Last full snapshot, the baseline `--delta` compares against. */
  last: Array<{ ref: string; text: string; css: string }> | null;
}

export function ensureRefRegistry(): RefRegistry {
  const g = globalThis as unknown as Record<string, unknown>;
  if (g.__browseRefs === undefined) {
    g.__browseRefs = {
      next: 1,
      refs: new WeakMap(),
      els: new Map(),
      last: null,
    };
  }
  return g.__browseRefs as RefRegistry;
}

ensureRefRegistry();
