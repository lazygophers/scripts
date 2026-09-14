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

/**
 * BiDi standard error codes, the subset this extension can produce, plus the
 * two private ones. A colon-prefixed code is a BiDi extension namespace (§3.3,
 * same rule as the `lg:` command modules), so the daemon's validator accepts
 * the 8 standard codes plus anything containing a colon —
 * `lib/browse_protocol.py` implements exactly that.
 *
 * `lg:browser not connected` is only ever produced daemon-side; it is listed
 * here so both ends carry the same enum.
 */
export type ErrorCode =
  | "invalid argument"
  | "no such element"
  | "no such frame"
  | "no such script"
  | "unknown command"
  | "unknown error"
  | "unsupported operation"
  | "lg:browser not connected"
  | "lg:user rejected";

/** Thrown by handlers to pick the error code instead of `unknown error`. */
export class CommandError extends Error {
  // Assigned in the body, not as a constructor parameter property: Node's
  // type-stripping (`node --test` on .ts sources) rejects those.
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = "CommandError";
    this.code = code;
  }
}

/**
 * Argument checks. Every handler needs the same three shapes, so they live here
 * next to `CommandError` instead of being retyped at each call site.
 *
 * `hint` is appended to the message when the plain name is not enough to act on
 * (`key must be a non-empty string, e.g. "Enter"`).
 */
export function asString(value: unknown, name: string, hint = ""): string {
  if (typeof value !== "string") {
    throw new CommandError("invalid argument", `${name} must be a string${hint}`);
  }
  return value;
}

/** Same, but empty is not a usable id, url or key either. */
export function requireString(value: unknown, name: string, hint = ""): string {
  if (typeof value !== "string" || value === "") {
    throw new CommandError("invalid argument", `${name} must be a non-empty string${hint}`);
  }
  return value;
}

/** `undefined` passes through untouched; anything else must be a string. */
export function optionalString(value: unknown, name: string, hint = ""): string | undefined {
  return value === undefined ? undefined : asString(value, name, hint);
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
