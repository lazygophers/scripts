import { CommandError, asString } from "../protocol.ts";
import { resolveContextOnce } from "./context.ts";
import { runInPage, type PageResult } from "./inject.ts";

/**
 * `lg:page.snapshot` — spec 6.4's discovery command: what on this page can be
 * clicked or typed into, and what locator reaches each one.
 *
 * Every entry carries both a `css` and an `xpath` that resolve to that exact
 * element, so the output pastes straight into `browse click`. It does
 * *not* go through the locator, so there is no waiting to switch off: it reads
 * the DOM as it stands right now.
 *
 * Every entry also carries a `ref` (`@e1`, `@e2`, …) that stays valid as long
 * as the element stays in the document — `browse click @e3` resolves straight
 * to the element, no re-parsing of css/xpath (see page-locate.ts's registry).
 *
 * `delta: true` compares against the previous snapshot in the same page load
 * and returns only `added` / `removed` / `changed` — the first call in a load
 * reports everything as added.
 *
 * `limit` (default 200) caps the list. A feed page has thousands of anchors and
 * a list that long answers nobody's question.
 */
const DEFAULT_LIMIT = 200;

export interface SnapshotDelta {
  added: SnapshotEntry[];
  removed: string[];
  changed: SnapshotEntry[];
}

export async function pageSnapshot(
  params: Record<string, unknown>,
): Promise<{ elements: SnapshotEntry[]; truncated: boolean } | {
  delta: SnapshotDelta;
  truncated: boolean;
}> {
  const limit = typeof params.limit === "number" && params.limit > 0
    ? Math.floor(params.limit)
    : DEFAULT_LIMIT;
  const delta = params.delta === true;
  const target = await resolveContextOnce(params);
  return runInPage(target, "ISOLATED", pageCollect, [limit, delta]);
}

/**
 * `lg:page.lifecycle` — wait for a document lifecycle milestone. Read-only,
 * no confirm: `waitFor` is one of `load` / `domcontentloaded` (mapped to
 * `complete` / `interactive` readyState below; readyState only moves forward,
 * so `complete` satisfies an `interactive` wait).
 *
 * This is deliberately its own ISOLATED-world handler rather than
 * `script.evaluate` + `document.readyState`: the latter runs in MAIN world and
 * takes the eval confirm — a wait command should not pop confirmations.
 *
 * Returns the readyState the page ended on; if `timeout` (ms) elapses first,
 * that is wherever the page got to, and the CLI decides whether it counts.
 */
export async function pageLifecycle(
  params: Record<string, unknown>,
): Promise<{ readyState: string; timedOut: boolean }> {
  const waitFor = asString(params.waitFor, "waitFor");
  if (waitFor !== "load" && waitFor !== "domcontentloaded") {
    throw new CommandError(
      "invalid argument",
      `waitFor must be "load" or "domcontentloaded", got ${JSON.stringify(waitFor)}`,
    );
  }
  const timeout = typeof params.timeout === "number" && params.timeout > 0
    ? params.timeout
    : 30000;
  const target = await resolveContextOnce(params);
  return runInPage(target, "ISOLATED", pageReadyState,
    [waitFor === "load" ? "complete" : "interactive", timeout]);
}

/**
 * Runs inside the page (same serialisation rules as pageCollect: closes over
 * nothing, helpers stay nested).
 */
export async function pageReadyState(
  want: string,
  timeoutMs: number,
): Promise<PageResult<{ readyState: string; timedOut: boolean }>> {
  try {
    const readyState = await new Promise<string>((resolve) => {
      const check = (): void => {
        if (document.readyState === "complete" || document.readyState === want) {
          cleanup();
          resolve(document.readyState);
        }
      };
      const onTimeout = (): void => {
        cleanup();
        resolve(document.readyState);
      };
      const cleanup = (): void => {
        document.removeEventListener("readystatechange", check);
        clearTimeout(timer);
      };
      const timer = setTimeout(onTimeout, timeoutMs);
      document.addEventListener("readystatechange", check);
      check();
    });
    const timedOut = want === "complete"
      ? readyState !== "complete"
      : readyState !== "complete" && readyState !== "interactive";
    return { ok: true, value: { readyState, timedOut } };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}

export interface SnapshotEntry {
  tag: string;
  /** `button` / `a` / the `type` of an input, whichever says the most. */
  role: string;
  /** Visible label, value or placeholder, trimmed to one line. */
  text: string;
  css: string;
  xpath: string;
  /** Stable per page load: the same element keeps this ref across snapshots. */
  ref: string;
}

/**
 * Runs inside the page. Serialised by `executeScript`, so it closes over
 * nothing — every helper is nested. Do not hoist anything out.
 */
export function pageCollect(
  limit: number,
  delta: boolean,
): PageResult<{ elements: SnapshotEntry[]; truncated: boolean } | {
  delta: SnapshotDelta;
  truncated: boolean;
}> {
  try {
    // The ref registry lives on globalThis (page-locate.js puts it there); a
    // serialised function cannot import it. Recreating it here would reset
    // every ref, so only build one when it is genuinely missing.
    const g = globalThis as unknown as Record<string, unknown>;
    let reg = g.__browseRefs as
      | { next: number; refs: WeakMap<Element, string>; els: Map<string, WeakRef<Element>>; last: Array<{ ref: string; text: string; css: string }> | null }
      | undefined;
    if (reg === undefined) {
      reg = { next: 1, refs: new WeakMap(), els: new Map(), last: null };
      g.__browseRefs = reg;
    }

    const SELECTOR = [
      "a[href]",
      "button",
      "input:not([type=hidden])",
      "select",
      "textarea",
      "summary",
      "[contenteditable=''],[contenteditable='true']",
      "[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem]",
      "[onclick]",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    const escape = (value: string): string =>
      typeof CSS !== "undefined" && typeof CSS.escape === "function"
        ? CSS.escape(value)
        : value.replace(/[^\w-]/g, "\\$&");

    const visible = (el: Element): boolean => {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) {
        return false;
      }
      const style = getComputedStyle(el);
      return style.visibility !== "hidden" && style.display !== "none";
    };

    const label = (el: Element): string => {
      const node = el as HTMLInputElement;
      const raw =
        (el as HTMLElement).innerText ||
        node.value ||
        node.placeholder ||
        el.getAttribute("aria-label") ||
        el.getAttribute("title") ||
        el.getAttribute("alt") ||
        // `innerText` is what a user sees, but it is layout-dependent and not
        // every engine (or test harness) has it; textContent is the floor.
        el.textContent ||
        "";
      return raw.replace(/\s+/g, " ").trim().slice(0, 80);
    };

    const role = (el: Element): string => {
      const explicit = el.getAttribute("role");
      if (explicit) {
        return explicit;
      }
      const tag = el.tagName.toLowerCase();
      const type = el.getAttribute("type");
      return tag === "input" && type ? `input[${type}]` : tag;
    };

    // Walk up until something unique: an id ends the path, otherwise each step
    // is pinned by its position among same-tag siblings.
    const cssPath = (el: Element): string => {
      const steps: string[] = [];
      let node: Element | null = el;
      while (node && node.nodeType === 1 && node !== document.documentElement) {
        if (node.id) {
          steps.unshift(`#${escape(node.id)}`);
          break;
        }
        const tag = node.tagName.toLowerCase();
        const parent: Element | null = node.parentElement;
        const twins = parent
          ? Array.from(parent.children).filter((c) => c.tagName === node!.tagName)
          : [];
        steps.unshift(
          twins.length > 1 ? `${tag}:nth-of-type(${twins.indexOf(node) + 1})` : tag,
        );
        node = parent;
      }
      return steps.join(" > ");
    };

    const xpath = (el: Element): string => {
      const steps: string[] = [];
      let node: Element | null = el;
      while (node && node.nodeType === 1) {
        const tag = node.tagName.toLowerCase();
        const parent: Element | null = node.parentElement;
        const twins = parent
          ? Array.from(parent.children).filter((c) => c.tagName === node!.tagName)
          : [];
        steps.unshift(twins.length > 1 ? `${tag}[${twins.indexOf(node) + 1}]` : tag);
        node = parent;
      }
      return `/${steps.join("/")}`;
    };

    const elements: SnapshotEntry[] = [];
    const all = document.querySelectorAll(SELECTOR);
    let truncated = false;
    for (let i = 0; i < all.length; i += 1) {
      const el = all[i]!;
      if (!visible(el)) {
        continue;
      }
      if (elements.length >= limit) {
        truncated = true;
        break;
      }
      let ref = reg.refs.get(el);
      if (ref === undefined) {
        ref = `@e${reg.next}`;
        reg.next += 1;
        reg.refs.set(el, ref);
        reg.els.set(ref, new WeakRef(el));
      }
      elements.push({
        tag: el.tagName.toLowerCase(),
        role: role(el),
        text: label(el),
        css: cssPath(el),
        xpath: xpath(el),
        ref,
      });
    }

    const prev = reg.last;
    reg.last = elements.map((e) => ({ ref: e.ref, text: e.text, css: e.css }));
    if (delta) {
      const prevRefs = new Set((prev ?? []).map((e) => e.ref));
      const current = new Map(elements.map((e) => [e.ref, e]));
      return {
        ok: true,
        value: {
          truncated,
          delta: {
            added: elements.filter((e) => !prevRefs.has(e.ref)),
            removed: (prev ?? []).filter((e) => !current.has(e.ref)).map((e) => e.ref),
            changed: elements.filter((e) => {
              const before = (prev ?? []).find((p) => p.ref === e.ref);
              return before !== undefined && (before.text !== e.text || before.css !== e.css);
            }),
          },
        },
      };
    }
    return { ok: true, value: { elements, truncated } };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}
