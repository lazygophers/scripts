import { CommandError, asString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { formatContext, parseContext, resolveContext, targetUrl, type Target } from "./context.ts";
import { execInPage, type PageResult } from "./inject.ts";

export { parseContext };

type Injected = PageResult<unknown>;

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
  const expression = asString(params.expression, "expression");
  const awaitPromise = params.awaitPromise !== false;
  const target = await resolveTarget(params);
  await confirmEval(target, "script.evaluate");

  const outcome = await execInPage(target, "MAIN",
    async (code: string, doAwait: boolean): Promise<Injected> => {
      try {
        const value: unknown = new Function(`return (${code})`)();
        return { ok: true, value: doAwait ? await value : value };
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : String(err) };
      }
    },
    [expression, awaitPromise]);
  return toScriptResult(target, outcome);
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
  const declaration = asString(params.functionDeclaration, "functionDeclaration");
  const argv = params.arguments ?? [];
  if (!Array.isArray(argv)) {
    throw new CommandError("invalid argument", "arguments must be an array of JSON values");
  }
  const awaitPromise = params.awaitPromise !== false;
  const target = await resolveTarget(params);
  await confirmEval(target, "script.callFunction");

  const outcome = await execInPage(target, "MAIN",
    async (
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
    [declaration, argv as unknown[], params.this ?? null, awaitPromise]);
  return toScriptResult(target, outcome);
}

async function confirmEval(target: Target, method: string): Promise<void> {
  await confirm({ action: "evalMainWorld", method, url: await targetUrl(target) });
}

function toScriptResult(target: Target, outcome: Injected): ScriptResult {
  if (!outcome.ok) {
    // The page threw. That is not a locator outcome, so it keeps the generic
    // code rather than going through `pageErrorCode`.
    throw new CommandError("unknown error", outcome.message);
  }
  return {
    type: "success",
    realm: formatContext(target.tabId, target.frameId),
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
