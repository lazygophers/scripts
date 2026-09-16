import { resolveContext } from "./context.ts";
import { runInPage, type PageResult } from "./inject.ts";

/**
 * `lg:page.snapshot` — spec 6.4's discovery command: what on this page can be
 * clicked or typed into, and what locator reaches each one.
 *
 * Every entry carries both a `css` and an `xpath` that resolve to that exact
 * element, so the output pastes straight into `browse input click`. It does
 * *not* go through the locator, so there is no waiting to switch off: it reads
 * the DOM as it stands right now.
 *
 * `limit` (default 200) caps the list. A feed page has thousands of anchors and
 * a list that long answers nobody's question.
 */
const DEFAULT_LIMIT = 200;

export async function pageSnapshot(
  params: Record<string, unknown>,
): Promise<{ elements: SnapshotEntry[]; truncated: boolean }> {
  const limit = typeof params.limit === "number" && params.limit > 0
    ? Math.floor(params.limit)
    : DEFAULT_LIMIT;
  const target = await resolveContext(params);
  return runInPage(target, "ISOLATED", pageCollect, [limit]);
}

export interface SnapshotEntry {
  tag: string;
  /** `button` / `a` / the `type` of an input, whichever says the most. */
  role: string;
  /** Visible label, value or placeholder, trimmed to one line. */
  text: string;
  css: string;
  xpath: string;
}

/**
 * Runs inside the page. Serialised by `executeScript`, so it closes over
 * nothing — every helper is nested. Do not hoist anything out.
 */
export function pageCollect(
  limit: number,
): PageResult<{ elements: SnapshotEntry[]; truncated: boolean }> {
  try {
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
      elements.push({
        tag: el.tagName.toLowerCase(),
        role: role(el),
        text: label(el),
        css: cssPath(el),
        xpath: xpath(el),
      });
    }
    return { ok: true, value: { elements, truncated } };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}
