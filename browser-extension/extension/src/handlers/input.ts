import { parseLocator, type LocateOptions, type Scheme } from "../locator.ts";
import { CommandError } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { resolveContext, targetUrl } from "./context.ts";
import { runInPage, type PageLocate, type PageResult } from "./inject.ts";

/**
 * `input.click` / `input.type` / `input.key` / `input.scroll`.
 *
 * **Every event these produce has `event.isTrusted === false`** (spec 5.4 #1).
 * Extension APIs have no way to dispatch a real input event — `chrome.debugger`
 * does, and spec 5.3 rules it out — so a site that checks `isTrusted` (some
 * banks, some payment and captcha widgets) will reject these. That is not
 * hidden: every result carries `"lg:isTrusted": false`.
 *
 * Actionability is checked at the moment of the action, not at location time:
 * the locator waits for *visible*, and `disabled` / `aria-disabled` /
 * `readonly` is re-read here, right before the event goes out.
 */
export async function inputClick(params: Record<string, unknown>): Promise<unknown> {
  return run("click", params, {});
}

export async function inputType(params: Record<string, unknown>): Promise<unknown> {
  const text = params.text;
  if (typeof text !== "string") {
    throw new CommandError("invalid argument", "text must be a string");
  }
  return run("type", params, { text, clear: params.clear !== false });
}

export async function inputKey(params: Record<string, unknown>): Promise<unknown> {
  const key = params.key;
  if (typeof key !== "string" || key === "") {
    throw new CommandError("invalid argument", `key must be a non-empty string, e.g. "Enter"`);
  }
  return run("key", params, {
    key,
    code: typeof params.code === "string" ? params.code : key,
    ctrlKey: params.ctrl === true,
    shiftKey: params.shift === true,
    altKey: params.alt === true,
    metaKey: params.meta === true,
  });
}

export async function inputScroll(params: Record<string, unknown>): Promise<unknown> {
  const { dx, dy } = params;
  if (dx !== undefined && typeof dx !== "number") {
    throw new CommandError("invalid argument", "dx must be a number");
  }
  if (dy !== undefined && typeof dy !== "number") {
    throw new CommandError("invalid argument", "dy must be a number");
  }
  return run("scroll", params, { dx: dx ?? 0, dy: dy ?? 0 });
}

type InputAction = "click" | "type" | "key" | "scroll";

async function run(
  action: InputAction,
  params: Record<string, unknown>,
  payload: Record<string, unknown>,
): Promise<unknown> {
  const selector = params.selector;
  if (selector !== undefined && typeof selector !== "string") {
    throw new CommandError("invalid argument", "selector must be a string");
  }
  // `input.scroll` and `input.key` are the only ones that work without one:
  // they fall back to the window and the focused element.
  if (selector === undefined && (action === "click" || action === "type")) {
    throw new CommandError("invalid argument", `${action} needs a selector`);
  }

  const locator = selector === undefined ? null : parseLocator(selector);
  const options: LocateOptions = {
    ...(typeof params.index === "number" ? { index: params.index } : {}),
    ...(params.all === true ? { all: true } : {}),
    ...(typeof params.timeout === "number" ? { timeout: params.timeout } : {}),
    ...(params.wait === false ? { wait: false } : {}),
  };
  const target = await resolveContext(params);
  // `js=` evaluates page expressions, so it needs the page realm (spec 6.4) —
  // which makes it arbitrary JS in the page, exactly what `script.evaluate`
  // does, just wearing a locator's clothes. Spec 4.4 lists it as high-risk for
  // that reason, so it takes the same confirm as `script.evaluate`. The other
  // three schemes stay in ISOLATED and are not high-risk.
  const world = locator?.scheme === "js" ? "MAIN" : "ISOLATED";
  if (world === "MAIN") {
    await confirm({
      action: "evalMainWorld",
      method: `input.${action}`,
      url: await targetUrl(target),
    });
  }

  const value = await runInPage(target, world, pageInput, [action, locator, options, payload]);
  return { ...(value as Record<string, unknown>), "lg:isTrusted": false };
}

/**
 * Runs inside the page. Serialised by `executeScript`, so it closes over
 * nothing: the locator comes off `globalThis` (page-locate.js put it there) and
 * every helper is nested. Do not hoist anything out of this function.
 */
export async function pageInput(
  action: InputAction,
  locator: { scheme: Scheme; value: string } | null,
  options: LocateOptions,
  payload: Record<string, unknown>,
): Promise<PageResult<Record<string, unknown>>> {
  try {
    const locate = (globalThis as unknown as Record<string, unknown>).__browseLocate as
      | PageLocate
      | undefined;
    if (locator !== null && typeof locate !== "function") {
      throw new Error("unknown error: locator was not injected into the page");
    }

    const view = document.defaultView;
    const elements =
      locator === null ? [] : await locate!(locator.scheme, locator.value, options);

    const describe = (el: Element): string => {
      const id = el.id ? `#${el.id}` : "";
      return `<${el.tagName.toLowerCase()}${id}>`;
    };

    const assertActionable = (el: Element): void => {
      const node = el as HTMLInputElement;
      if (node.disabled === true || el.getAttribute("aria-disabled") === "true") {
        throw new Error(`invalid argument: element is disabled: ${describe(el)}`);
      }
      if (action === "type" && (node.readOnly === true || el.getAttribute("readonly") !== null)) {
        throw new Error(`invalid argument: element is readonly: ${describe(el)}`);
      }
    };

    const mouse = (el: Element, type: string): void => {
      el.dispatchEvent(
        new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          composed: true,
          ...(view === null ? {} : { view }),
        }),
      );
    };

    const click = (el: Element): void => {
      assertActionable(el);
      (el as HTMLElement).scrollIntoView?.({ block: "center" });
      for (const type of ["pointerover", "mouseover", "pointerdown", "mousedown"]) {
        mouse(el, type);
      }
      (el as HTMLElement).focus?.();
      for (const type of ["pointerup", "mouseup", "click"]) {
        mouse(el, type);
      }
    };

    const setValue = (el: Element, text: string): void => {
      const editable =
        (el as HTMLElement).isContentEditable === true ||
        (el.getAttribute("contenteditable") ?? "false") !== "false";
      if (editable) {
        (el as HTMLElement).textContent = text;
        return;
      }
      // React and friends patch the value setter to track changes; going
      // through the prototype descriptor is what makes them notice.
      const proto = Object.getPrototypeOf(el) as object;
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      if (descriptor?.set === undefined) {
        throw new Error(
          `invalid argument: cannot type into ${describe(el)}; it has no value and is not contenteditable`,
        );
      }
      descriptor.set.call(el, text);
    };

    const type = (el: Element): void => {
      assertActionable(el);
      (el as HTMLElement).focus?.();
      if (payload.clear === true) {
        setValue(el, "");
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      setValue(el, String(payload.text ?? ""));
      // One `input` for the whole string, not one per character: per-key
      // replay would be slower and still not trusted, so it buys nothing.
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const key = (el: EventTarget): void => {
      const init = {
        bubbles: true,
        cancelable: true,
        composed: true,
        key: String(payload.key),
        code: String(payload.code),
        ctrlKey: payload.ctrlKey === true,
        shiftKey: payload.shiftKey === true,
        altKey: payload.altKey === true,
        metaKey: payload.metaKey === true,
      };
      el.dispatchEvent(new KeyboardEvent("keydown", init));
      if (String(payload.key).length === 1) {
        el.dispatchEvent(new KeyboardEvent("keypress", init));
      }
      el.dispatchEvent(new KeyboardEvent("keyup", init));
    };

    const scroll = (el: Element | null): { x: number; y: number } => {
      const dx = Number(payload.dx ?? 0);
      const dy = Number(payload.dy ?? 0);
      if (el === null) {
        view?.scrollBy?.(dx, dy);
        return { x: view?.scrollX ?? 0, y: view?.scrollY ?? 0 };
      }
      if (dx === 0 && dy === 0) {
        (el as HTMLElement).scrollIntoView?.({ block: "center" });
      } else {
        el.scrollBy?.(dx, dy);
      }
      return { x: el.scrollLeft, y: el.scrollTop };
    };

    if (action === "scroll") {
      const position = scroll(elements[0] ?? null);
      return { ok: true, value: { scrolled: position } };
    }
    if (action === "key") {
      const targets: EventTarget[] =
        elements.length > 0 ? elements : [document.activeElement ?? document.body];
      for (const el of targets) {
        key(el);
      }
      return { ok: true, value: { key: payload.key, count: targets.length } };
    }

    for (const el of elements) {
      if (action === "click") click(el);
      else type(el);
    }
    return {
      ok: true,
      value: { count: elements.length, element: elements.map(describe) },
    };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}
