import { JSDOM } from "jsdom";
import { locateInPage } from "../src/locator.ts";

type Any = Record<string, unknown>;

/**
 * Install a fake `chrome` global. Only the namespaces a test names exist, so a
 * handler that reaches for anything else trips `requireApi` — which is the
 * cross-browser refusal path (spec 5.5) and worth catching in tests.
 */
export function installChrome(namespaces: Any): Any {
  const chrome = namespaces as Any;
  (globalThis as Any).chrome = chrome;
  return chrome;
}

export function clearChrome(): void {
  delete (globalThis as Any).chrome;
}

/**
 * `chrome.scripting` that really runs the injected function in this process,
 * so the page-side code under test is the code that ships. `files:` stands in
 * for page-locate.js by putting the real locator on `globalThis`.
 */
export function scriptingMock(): {
  executeScript: (injection: Any) => Promise<{ result: unknown }[]>;
  calls: Any[];
} {
  const calls: Any[] = [];
  return {
    calls,
    executeScript: async (injection: Any) => {
      calls.push(injection);
      if (Array.isArray(injection.files)) {
        (globalThis as Any).__browseLocate = locateInPage;
        return [{ result: undefined }];
      }
      const func = injection.func as (...args: unknown[]) => unknown;
      const args = (injection.args as unknown[]) ?? [];
      return [{ result: await func(...args) }];
    },
  };
}

/** Install a jsdom realm as the globals page-side code reads off `globalThis`. */
export function page(html: string): JSDOM {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, {
    url: "https://example.test/page",
  });
  const g = globalThis as Any;
  for (const name of [
    "document",
    "Element",
    "HTMLElement",
    "XPathResult",
    "MutationObserver",
    "MouseEvent",
    "KeyboardEvent",
    "Event",
    "localStorage",
  ]) {
    g[name] = (dom.window as unknown as Any)[name];
  }
  return dom;
}

/** Assert that `body` rejects with a CommandError carrying `code`. */
export async function rejectsWith(
  body: () => Promise<unknown>,
  code: string,
): Promise<Error> {
  try {
    await body();
  } catch (err) {
    const error = err as Error & { code?: string };
    if (error.code !== code) {
      throw new Error(`expected error code ${code}, got ${error.code}: ${error.message}`);
    }
    return error;
  }
  throw new Error(`expected a rejection with code ${code}`);
}
