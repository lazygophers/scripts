import { CommandError } from "../protocol.ts";

/**
 * Confirmation hook for the high-risk actions of spec 4.4. The *policy* lives
 * on the Python side (T08): `confirm_mode` is read from
 * `~/.config/lazygophers/scripts/browse.yaml`, the per-domain allow list and
 * the audit log are the daemon's. This module is only the call point.
 *
 * TODO(T08): call `setConfirmHook` from background.ts with an implementation
 * that round-trips to the daemon over the native port. Until it does, the
 * default hook allows everything — which is exactly `confirm_mode: silent`,
 * the documented default, not an accidental bypass.
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
