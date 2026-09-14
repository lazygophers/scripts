import { CommandError } from "../protocol.js";
import { browsingContextGetTree } from "./browsingContext.js";
import { scriptEvaluate } from "./script.js";

export type Handler = (params: Record<string, unknown>) => Promise<unknown>;

/**
 * The command table. Key is the wire `method`, value returns the `result`
 * payload of a Success reply. Throw `CommandError` to pick an error code;
 * anything else becomes `unknown error`.
 *
 * T06 adds the remaining 14 command groups by creating one module per BiDi
 * module under this directory and adding entries here. Nothing else changes.
 */
export const HANDLERS: Record<string, Handler> = {
  "browsingContext.getTree": browsingContextGetTree,
  "script.evaluate": scriptEvaluate,
};

export async function dispatch(
  method: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  const handler = HANDLERS[method];
  if (!handler) {
    throw new CommandError(
      method.includes(".") ? "unsupported operation" : "unknown command",
      `no handler for ${method}`,
    );
  }
  return handler(params);
}
