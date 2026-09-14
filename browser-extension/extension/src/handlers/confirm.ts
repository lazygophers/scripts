import { CommandError } from "../protocol.ts";

/**
 * Confirmation for the high-risk actions of spec 4.4. The *policy* lives on the
 * Python side (T08/T11): `confirm_mode`, the per-domain allow list, the deny
 * list and the audit log are all the daemon's. The daemon decides *whether* to
 * ask; this module only knows *how* to ask.
 *
 * Two entry points, one hook:
 *
 * - `lg:confirm.request` — the daemon gates a command before forwarding it and
 *   sends this when it needs a human. `confirmRequest` answers `{approved}`.
 * - `confirm()` — called inline by the handlers at the exact point of risk.
 *   Every command reaching a handler has already passed the daemon's gate, so
 *   in production this is a second line of defence, not the gate.
 *
 * The hook `background.ts` installs is the UI (a popup window). It remembers
 * the answer for a few seconds, so the handler's inline `confirm()` for the
 * very command the user just approved does not pop a second identical dialog.
 * That cache is UI debouncing, not policy — no policy is duplicated here.
 *
 * The default hook allows everything, which is `confirm_mode: silent`, the
 * documented default, and matters only for unit tests: `background.ts` always
 * replaces it.
 */
export type RiskyAction =
  | "readCookies"
  | "writeCookies"
  | "readLocalStorage"
  | "writeLocalStorage"
  | "evalMainWorld"
  | "download"
  | "readHistory"
  | "writeHistory"
  | "readBookmarks"
  | "writeBookmarks";

export interface ConfirmRequest {
  action: RiskyAction;
  /** The wire method that triggered it, e.g. `storage.getCookies`. */
  method: string;
  /** Page URL the action targets, or null when it is browser-wide. */
  url: string | null;
}

export type ConfirmHook = (request: ConfirmRequest) => Promise<boolean>;

let hook: ConfirmHook = async () => true;

export function setConfirmHook(fn: ConfirmHook): void {
  hook = fn;
}

/** Throws when the hook says no. Callers do not catch it. */
export async function confirm(request: ConfirmRequest): Promise<void> {
  if (await hook(request)) {
    return;
  }
  throw new CommandError(
    "lg:user rejected",
    `user denied ${request.action} for ${request.method} on ${request.url ?? "the browser"}`,
  );
}

const ACTIONS = new Set<string>([
  "readCookies",
  "writeCookies",
  "readLocalStorage",
  "writeLocalStorage",
  "evalMainWorld",
  "download",
  "readHistory",
  "writeHistory",
  "readBookmarks",
  "writeBookmarks",
]);

/**
 * `lg:confirm.request` — the daemon asking for a decision. Never throws: a
 * refusal is a `{approved: false}` result, because the daemon is asking a
 * question, not running a command. Anything unexpected is also `false`, so a
 * bug here fails closed.
 */
export async function confirmRequest(
  params: Record<string, unknown>,
): Promise<{ approved: boolean }> {
  const action = params.action;
  const method = params.method;
  if (typeof action !== "string" || !ACTIONS.has(action) || typeof method !== "string") {
    throw new CommandError(
      "invalid argument",
      `lg:confirm.request needs a known action and a method, got ${JSON.stringify(params)}`,
    );
  }
  const url = typeof params.url === "string" ? params.url : null;
  try {
    await confirm({ action: action as RiskyAction, method, url });
    return { approved: true };
  } catch {
    return { approved: false };
  }
}
