import { CommandError } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { parseContext, resolveContext, targetUrl, type Target } from "./context.ts";

export { parseContext };

type Injected = { ok: true; value: unknown } | { ok: false; message: string };

interface ScriptResult {
  type: "success";
  realm: string;
  result: { value: unknown };
}

/**
 * BiDi `script.evaluate`. Params: `{ expression, target: { context } | context,
 * awaitPromise }`. `context` is a context id from `browsingContext.getTree`
 * (`<tabId>` or `<tabId>.<frameId>`); omitted means the active tab.
 *
 * The expression crosses into MAIN world as a *string argument*, evaluated by
 * `new Function` on the page side. That is data, not remote code, so it stays
 * inside MV3's no-remote-code rule (same trick as hangwin/mcp-chrome).
 *
 * MAIN-world evaluation is a high-risk action (spec 4.4), so it goes through
 * the confirm hook.
 *
 * The returned value is whatever survives structured cloning out of the page;
 * a DOM node comes back as an empty object. Return primitives or JSON.
 */
export async function scriptEvaluate(params: Record<string, unknown>): Promise<ScriptResult> {
  const expression = params.expression;
  if (typeof expression !== "string") {
    throw new CommandError("invalid argument", "expression must be a string");
  }
  const awaitPromise = params.awaitPromise !== false;
  const target = await resolveTarget(params);
  await confirmEval(target, "script.evaluate");

  const outcome = await inject(target, {
    args: [expression, awaitPromise],
    func: async (code: string, doAwait: boolean): Promise<Injected> => {
      try {
        const value: unknown = new Function(`return (${code})`)();
        return { ok: true, value: doAwait ? await value : value };
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : String(err) };
      }
    },
  });
  return shape(target, outcome);
}

/**
 * BiDi `script.callFunction`. `functionDeclaration` is a function *expression*
 * source; `arguments` and `this` are plain JSON values (there are no remote
 * object handles in v1, so an element cannot be passed in — locate it inside
 * the function body instead).
 */
export async function scriptCallFunction(
  params: Record<string, unknown>,
): Promise<ScriptResult> {
  const declaration = params.functionDeclaration;
  if (typeof declaration !== "string") {
    throw new CommandError("invalid argument", "functionDeclaration must be a string");
  }
  const argv = params.arguments ?? [];
  if (!Array.isArray(argv)) {
    throw new CommandError("invalid argument", "arguments must be an array of JSON values");
  }
  const awaitPromise = params.awaitPromise !== false;
  const target = await resolveTarget(params);
  await confirmEval(target, "script.callFunction");

  const outcome = await inject(target, {
    args: [declaration, argv as unknown[], params.this ?? null, awaitPromise],
    func: async (
      code: string,
      callArgs: unknown[],
      thisArg: unknown,
      doAwait: boolean,
    ): Promise<Injected> => {
      try {
        const fn: unknown = new Function(`return (${code})`)();
        if (typeof fn !== "function") {
          return { ok: false, message: "functionDeclaration did not evaluate to a function" };
        }
        const value: unknown = (fn as (...a: unknown[]) => unknown).apply(thisArg, callArgs);
        return { ok: true, value: doAwait ? await value : value };
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : String(err) };
      }
    },
  });
  return shape(target, outcome);
}

async function confirmEval(target: Target, method: string): Promise<void> {
  await confirm({ action: "evalMainWorld", method, url: await targetUrl(target) });
}

async function inject(
  target: Target,
  injection: { args: unknown[]; func: (...args: never[]) => Promise<Injected> },
): Promise<Injected> {
  const [result] = await chrome.scripting.executeScript({
    target:
      target.frameId === undefined
        ? { tabId: target.tabId }
        : { tabId: target.tabId, frameIds: [target.frameId] },
    world: "MAIN",
    args: injection.args as never[],
    func: injection.func,
  });
  const outcome = result?.result as Injected | undefined;
  if (outcome === undefined) {
    throw new CommandError("unknown error", "script produced no result");
  }
  return outcome;
}

function shape(target: Target, outcome: Injected): ScriptResult {
  if (!outcome.ok) {
    throw new CommandError("unknown error", outcome.message);
  }
  return {
    type: "success",
    realm:
      target.frameId === undefined ? String(target.tabId) : `${target.tabId}.${target.frameId}`,
    result: { value: outcome.value },
  };
}

/** BiDi nests the context under `target`; the CLI passes it flat. Accept both. */
async function resolveTarget(params: Record<string, unknown>): Promise<Target> {
  const target = params.target;
  if (target !== undefined && (typeof target !== "object" || target === null)) {
    throw new CommandError("invalid argument", "target must be an object");
  }
  return resolveContext({ ...params, ...(target as Record<string, unknown> | undefined) });
}
