/**
 * Element location, spec 6.4. Four schemes behind a prefix; no prefix is `css=`.
 *
 * `locateInPage` runs *inside the page*: it is handed to
 * `chrome.scripting.executeScript({ func })`, which serialises the function
 * source and loses every closure. So it must reference nothing outside itself
 * — no `chrome.*`, no imports, no module-level constants. Every helper is
 * nested on purpose; do not hoist them out.
 *
 * `js=` has to evaluate in the page realm, so its executeScript call needs
 * `world: "MAIN"` (same data-not-remote-code trick as handlers/script.ts:
 * the expression crosses as a string argument). The other three schemes work
 * from ISOLATED, which is where they should stay.
 */

// Type-only: this module must carry no runtime import (see above).
import type { ErrorCode } from "./protocol.js";

export type Scheme = "css" | "text" | "text*" | "xpath" | "js";

export interface Locator {
  scheme: Scheme;
  value: string;
}

export interface LocateOptions {
  /** Which match to take when several hit. Default 0. Ignored when `all`. */
  index?: number;
  /** Return every match instead of one. */
  all?: boolean;
  /** Auto-wait budget in ms. Default 5000. */
  timeout?: number;
  /** Auto-wait toggle. Default true. */
  wait?: boolean;
}

const SCHEMES: readonly string[] = ["css", "text", "text*", "xpath", "js"];

/**
 * Split on the *first* `=` only, so a value may contain more of them
 * (`css=a[href="x=1"]`). An unknown prefix is not an error: the whole string
 * is then a CSS selector, which is what `a[href="x=1"]` needs.
 */
export function parseLocator(selector: string): Locator {
  const eq = selector.indexOf("=");
  const prefix = eq > 0 ? selector.slice(0, eq) : "";
  if (SCHEMES.includes(prefix)) {
    return { scheme: prefix as Scheme, value: selector.slice(eq + 1) };
  }
  return { scheme: "css", value: selector };
}

const PAGE_ERROR_CODES: readonly ErrorCode[] = ["no such element", "invalid argument"];

/**
 * `locateInPage` throws plain `Error`s because `CommandError` does not exist in
 * the page realm; the message carries the BiDi code as its prefix. Call this on
 * the extension side to recover the code:
 * `throw new CommandError(pageErrorCode(err), String(err))`.
 */
export function pageErrorCode(err: unknown): ErrorCode {
  const message = err instanceof Error ? err.message : String(err);
  return PAGE_ERROR_CODES.find((c) => message.includes(c)) ?? "unknown error";
}

/**
 * Resolve a locator against the current document. Returns the picked elements
 * (one, unless `all`), or rejects with `no such element` / `invalid argument`.
 *
 * With `wait` on (the default) the picked elements must also be visible, so a
 * hidden match keeps the wait going and then times out. Pass `wait: false` to
 * read a hidden element.
 */
export async function locateInPage(
  scheme: Scheme,
  value: string,
  options: LocateOptions = {},
): Promise<Element[]> {
  const all = options.all === true;
  const index = options.index ?? 0;
  const timeout = options.timeout ?? 5000;
  const wait = options.wait !== false;

  // ponytail: style-only visibility, no layout box check. jsdom has no layout
  // (every rect is 0x0), so a getClientRects() test would call everything
  // invisible. Swap in el.checkVisibility({checkVisibilitySize:true}) once
  // these tests run in a headless browser.
  const visible = (el: Element): boolean => {
    const win = el.ownerDocument.defaultView;
    if (!win) return false;
    for (let node: Element | null = el; node !== null; node = node.parentElement) {
      if ((node as HTMLElement).hidden === true) return false;
      const style = win.getComputedStyle(node);
      if (style.display === "none") return false;
      if (style.visibility === "hidden" || style.visibility === "collapse") return false;
      if (style.opacity !== "" && Number(style.opacity) === 0) return false;
    }
    return true;
  };

  const normalize = (s: string): string => s.replace(/\s+/g, " ").trim();

  const textOf = (el: Element): string =>
    normalize((el as HTMLElement).innerText ?? el.textContent ?? "");

  const byText = (): Element[] => {
    const want = normalize(value);
    const skip = ["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD"];
    const hits = Array.from(document.querySelectorAll("*")).filter(
      (el) =>
        !skip.includes(el.tagName) &&
        visible(el) &&
        (scheme === "text" ? textOf(el) === want : textOf(el).includes(want)),
    );
    // An ancestor's text contains its children's, so keep only the deepest hits.
    return hits.filter((el) => !hits.some((other) => other !== el && el.contains(other)));
  };

  const byXPath = (): Element[] => {
    const snapshot = document.evaluate(
      value,
      document,
      null,
      XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
      null,
    );
    const out: Element[] = [];
    for (let i = 0; i < snapshot.snapshotLength; i += 1) {
      const node = snapshot.snapshotItem(i);
      if (node instanceof Element) out.push(node);
    }
    return out;
  };

  const byJs = (): Element[] => {
    const result: unknown = new Function(`return (${value})`)();
    if (!(result instanceof Element)) {
      throw new Error(
        `invalid argument: js= must evaluate to an Element, got ${Object.prototype.toString.call(result)}`,
      );
    }
    return [result];
  };

  const match = (): Element[] => {
    try {
      if (scheme === "css") return Array.from(document.querySelectorAll(value));
      if (scheme === "xpath") return byXPath();
      if (scheme === "js") return byJs();
      return byText();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.startsWith("invalid argument")) throw err;
      throw new Error(`invalid argument: bad ${scheme}= locator: ${message}`);
    }
  };

  const attempt = (): Element[] | null => {
    const found = match();
    const picked = all ? found : found.slice(index, index + 1);
    if (picked.length === 0) return null;
    if (wait && !picked.every(visible)) return null;
    return picked;
  };

  const first = attempt();
  if (first !== null) return first;
  if (!wait) throw new Error(`no such element: ${scheme}=${value}`);

  return new Promise<Element[]>((resolve, reject) => {
    // MutationObserver catches DOM edits; the interval is the fallback for what
    // it cannot see (a CSS transition revealing an element, a lazy image
    // finishing layout) without being a busy loop.
    const observer = new MutationObserver(() => check());
    const poll = setInterval(() => check(), 100);
    const deadline = setTimeout(() => {
      settle(null, new Error(`no such element: ${scheme}=${value} (waited ${timeout}ms)`));
    }, timeout);

    function settle(found: Element[] | null, err?: Error): void {
      observer.disconnect();
      clearInterval(poll);
      clearTimeout(deadline);
      if (err) reject(err);
      else resolve(found as Element[]);
    }

    function check(): void {
      try {
        const found = attempt();
        if (found !== null) settle(found);
      } catch (err) {
        settle(null, err instanceof Error ? err : new Error(String(err)));
      }
    }

    observer.observe(document, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });
    check();
  });
}
