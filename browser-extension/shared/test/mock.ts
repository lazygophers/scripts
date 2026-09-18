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

/**
 * Install a jsdom realm as the globals page-side code reads off `globalThis`.
 *
 * `url` and `contentType` are what viewer's takeover test reads, so both are
 * settable. jsdom only accepts HTML/XML in its own `contentType` option, and a
 * plain-text page is exactly the case under test — hence the redefine instead.
 */
export function page(
  html: string,
  { url = "https://example.test/page", contentType = "text/html" } = {},
): JSDOM {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, { url });
  Object.defineProperty(dom.window.document, "contentType", {
    value: contentType,
    configurable: true,
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
  ]) {
    g[name] = (dom.window as unknown as Any)[name];
  }
  // jsdom 对 `file://` 这类不透明来源会在取 `localStorage` 时直接抛错，而本地文件
  // 页面正是被测的场景，所以这一个单独试着取，取不到就不装。
  try {
    g.localStorage = dom.window.localStorage;
  } catch {
    delete g.localStorage;
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
  // `chrome.storage.onChanged` 是跨标签页广播设置变更的那条路（viewer 的主题靠它
  // 让已经开着的页面跟着换），所以桩里也要把它接上，`set` 之后真的通知下去。
  const listeners: ((changes: Any, area: string) => void)[] = [];
  const local = {
    get: async (key: string) => (key in store ? { [key]: store[key] } : {}),
    set: async (items: Any) => {
      state.sets += 1;
      if (state.failNext > 0) {
        state.failNext -= 1;
        throw new Error("QUOTA_BYTES quota exceeded");
      }
      Object.assign(store, items);
      const changes: Any = {};
      for (const [key, value] of Object.entries(items)) changes[key] = { newValue: value };
      for (const listener of listeners) listener(changes, "local");
    },
    remove: async (key: string) => {
      delete store[key];
    },
  };
  const onChanged = {
    addListener: (listener: (changes: Any, area: string) => void) => void listeners.push(listener),
  };
  const existing = ((globalThis as Any).chrome as Any) ?? {};
  installChrome({ ...existing, storage: { local, onChanged } });
  return state;
}
