import { CommandError } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi } from "./context.ts";

/**
 * `lg:history.search` / `lg:history.delete`. Not a BiDi module — browsing
 * history has no BiDi equivalent — so it carries the private `lg:` prefix
 * (spec 3.3). Reading history is a high-risk action (spec 4.4).
 */
export async function historySearch(
  params: Record<string, unknown>,
): Promise<{ items: chrome.history.HistoryItem[] }> {
  requireApi("history", "reading browsing history");
  await confirm({ action: "readHistory", method: "lg:history.search", url: null });

  const text = params.text === undefined ? "" : params.text;
  if (typeof text !== "string") {
    throw new CommandError("invalid argument", "text must be a string");
  }
  const items = await chrome.history.search({
    text,
    ...(typeof params.startTime === "number" ? { startTime: params.startTime } : {}),
    ...(typeof params.endTime === "number" ? { endTime: params.endTime } : {}),
    ...(typeof params.maxResults === "number" ? { maxResults: params.maxResults } : {}),
  });
  return { items };
}

/**
 * `lg:history.delete`. Either `url` (one page) or `startTime` + `endTime` (a
 * range). Deleting the whole history is not offered: `deleteAll` is one typo
 * away from unrecoverable, and nothing in the spec asks for it.
 */
export async function historyDelete(
  params: Record<string, unknown>,
): Promise<{ deleted: "url" | "range" }> {
  requireApi("history", "deleting browsing history");
  const { url, startTime, endTime } = params;

  if (typeof url === "string" && url !== "") {
    await confirm({ action: "writeHistory", method: "lg:history.delete", url });
    await chrome.history.deleteUrl({ url });
    return { deleted: "url" };
  }
  if (typeof startTime === "number" && typeof endTime === "number") {
    await confirm({ action: "writeHistory", method: "lg:history.delete", url: null });
    await chrome.history.deleteRange({ startTime, endTime });
    return { deleted: "range" };
  }
  throw new CommandError(
    "invalid argument",
    "pass url, or both startTime and endTime; deleting all history is not supported",
  );
}
