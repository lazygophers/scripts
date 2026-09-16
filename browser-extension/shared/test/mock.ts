import { JSDOM } from "jsdom";

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

/**
 * 一个内存版 `chrome.storage.local`。策略和审计都存在那里，所以几乎每个用例都要它。
 *
 * `failNext` 让 `set` 抛一次配额错——这是撞 10 MB 上限时 `chrome.storage` 的真实行为，
 * 也是环形淘汰唯一的触发条件。真造 10 MB 数据太慢，用它更快也更准。
 */
export function storageMock(initial: Any = {}): {
  data: Any;
  /** 还剩几次 set 会抛配额错。 */
  failNext: number;
  sets: number;
} {
  const store: Any = { ...initial };
  const state = { data: store, failNext: 0, sets: 0 };
  const local = {
    get: async (key: string) => (key in store ? { [key]: store[key] } : {}),
    set: async (items: Any) => {
      state.sets += 1;
      if (state.failNext > 0) {
        state.failNext -= 1;
        throw new Error("QUOTA_BYTES quota exceeded");
      }
      Object.assign(store, items);
    },
    remove: async (key: string) => {
      delete store[key];
    },
  };
  const existing = ((globalThis as Any).chrome as Any) ?? {};
  installChrome({ ...existing, storage: { local } });
  return state;
}
