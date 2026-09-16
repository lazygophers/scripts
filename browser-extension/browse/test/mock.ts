import { locateInPage } from "../src/locator.ts";

// The browser-neutral halves of the scaffolding live in the shared layer; they
// are re-exported here so every test keeps importing one file.
export {
  clearChrome,
  installChrome,
  page,
  rejectsWith,
  storageMock,
} from "../../shared/test/mock.ts";

type Any = Record<string, unknown>;

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
