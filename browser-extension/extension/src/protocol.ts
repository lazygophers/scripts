/**
 * Wire envelope, spec 3.3. Shape is WebDriver BiDi's; private capabilities
 * carry a `lg:` prefix (BiDi 3.3 reserves colon-prefixed module names).
 */

export const NATIVE_HOST = "com.lazygophers.browse";

export interface Command {
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

export interface Success {
  type: "success";
  id: number;
  result: unknown;
}

export interface ErrorReply {
  type: "error";
  id: number;
  error: ErrorCode;
  message: string;
}

export interface Event {
  type: "event";
  method: string;
  params: Record<string, unknown>;
}

export type Outbound = Success | ErrorReply | Event;

/** BiDi standard error codes, the subset this extension can produce. */
export type ErrorCode =
  | "invalid argument"
  | "no such element"
  | "no such frame"
  | "no such script"
  | "unknown command"
  | "unknown error"
  | "unsupported operation";

/** Thrown by handlers to pick the error code instead of `unknown error`. */
export class CommandError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "CommandError";
  }
}

export function isCommand(value: unknown): value is Command {
  const c = value as Command | null;
  return (
    typeof c === "object" &&
    c !== null &&
    typeof c.id === "number" &&
    typeof c.method === "string"
  );
}
