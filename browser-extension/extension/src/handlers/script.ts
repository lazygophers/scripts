import { CommandError } from "../protocol.js";

type Injected = { ok: true; value: unknown } | { ok: false; message: string };

/**
 * BiDi `script.evaluate`. Params: `{ expression, target: { context }, awaitPromise }`.
 * `context` is a context id from `browsingContext.getTree` (`<tabId>` or
 * `<tabId>.<frameId>`); omitted means the active tab of the current window.
 *
 * The expression crosses into MAIN world as a *string argument*, evaluated by
 * `new Function` on the page side. That is data, not remote code, so it stays
 * inside MV3's no-remote-code rule (same trick as hangwin/mcp-chrome).
 */
export async function scriptEvaluate(
  params: Record<string, unknown>,
): Promise<{ type: "success"; realm: string; result: { value: unknown } }> {
  const expression = params.expression;
  if (typeof expression !== "string") {
    throw new CommandError("invalid argument", "expression must be a string");
  }
  const awaitPromise = params.awaitPromise !== false;
  const { tabId, frameId } = await resolveTarget(params.target);

  const [injection] = await chrome.scripting.executeScript({
    target: frameId === undefined ? { tabId } : { tabId, frameIds: [frameId] },
    world: "MAIN",
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

  const outcome = injection?.result as Injected | undefined;
  if (!outcome) {
    throw new CommandError("unknown error", "script produced no result");
  }
  if (!outcome.ok) {
    throw new CommandError("unknown error", outcome.message);
  }
  return {
    type: "success",
    realm: frameId === undefined ? String(tabId) : `${tabId}.${frameId}`,
    result: { value: outcome.value },
  };
}

async function resolveTarget(
  target: unknown,
): Promise<{ tabId: number; frameId: number | undefined }> {
  const context = (target as { context?: unknown } | undefined)?.context;
  if (context === undefined) {
    const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (active?.id === undefined) {
      throw new CommandError("no such frame", "no active tab");
    }
    return { tabId: active.id, frameId: undefined };
  }
  if (typeof context !== "string") {
    throw new CommandError("invalid argument", "target.context must be a string");
  }
  return parseContext(context);
}

export function parseContext(context: string): {
  tabId: number;
  frameId: number | undefined;
} {
  const [tabPart, framePart] = context.split(".", 2);
  const tabId = Number(tabPart);
  if (!Number.isInteger(tabId)) {
    throw new CommandError("no such frame", `bad context id ${context}`);
  }
  if (framePart === undefined) {
    return { tabId, frameId: undefined };
  }
  const frameId = Number(framePart);
  if (!Number.isInteger(frameId)) {
    throw new CommandError("no such frame", `bad context id ${context}`);
  }
  return { tabId, frameId };
}
