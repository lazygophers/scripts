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
