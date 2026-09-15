import type { locateInPage } from "../locator.ts";
import { pageErrorCode } from "../locator.ts";
import { CommandError } from "../protocol.ts";
import type { Target } from "./context.ts";

/** Built by build.mjs from src/page-locate.ts. */
const LOCATE_FILE = "page-locate.js";

/**
 * Page functions cannot throw `CommandError` (it does not exist over there) and
 * a rejected promise from `executeScript` loses the message, so they return
 * this instead and `runInPage` turns it back into a `CommandError`.
 */
export type PageResult<T> = { ok: true; value: T } | { ok: false; message: string };

/**
 * The locator, re-exposed on the page's globalThis by page-locate.js. Page
 * functions read it off `globalThis`; they must not import anything, not even
 * from this module, because `executeScript({ func })` serialises the function
 * source and every closure over a module binding becomes a ReferenceError.
 */
export type PageLocate = typeof locateInPage;

/**
 * Run `func` inside the target page with the locator available.
 *
 * `world` is "MAIN" only when the page's own realm is needed (`js=` locators,
 * `script.*`); everything else stays ISOLATED. Both worlds share the injected
 * file because `files:` honours `world` too.
 */
export async function runInPage<Args extends unknown[], T>(
  target: Target,
  world: "ISOLATED" | "MAIN",
  func: (...args: Args) => PageResult<T> | Promise<PageResult<T>>,
  args: Args,
): Promise<T> {
  await chrome.scripting.executeScript({
    target: scriptTarget(target),
    world,
    files: [LOCATE_FILE],
  });
  const outcome = await execInPage(target, world, func, args);
  if (!outcome.ok) {
    throw new CommandError(pageErrorCode(outcome.message), outcome.message);
  }
  return outcome.value;
}

/**
 * The same injection without the locator file, and without turning a failed
 * `PageResult` into an error — `script.*` needs neither (its failures are the
 * page's own exceptions, not locator outcomes, so it picks its own error code).
 */
export async function execInPage<Args extends unknown[], T>(
  target: Target,
  world: "ISOLATED" | "MAIN",
  func: (...args: Args) => PageResult<T> | Promise<PageResult<T>>,
  args: Args,
): Promise<PageResult<T>> {
  const [injection] = await chrome.scripting.executeScript({
    target: scriptTarget(target),
    world,
    func,
    args,
  });
  const outcome = injection?.result as PageResult<T> | undefined;
  if (outcome === undefined) {
    throw new CommandError("unknown error", "script produced no result");
  }
  return outcome;
}

/** `Target` in the shape `chrome.scripting` wants. */
function scriptTarget(target: Target): chrome.scripting.InjectionTarget {
  return target.frameId === undefined
    ? { tabId: target.tabId }
    : { tabId: target.tabId, frameIds: [target.frameId] };
}
