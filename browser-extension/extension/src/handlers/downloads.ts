import { CommandError } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi } from "./context.ts";

/**
 * `lg:downloads.start` / `list` / `cancel`. Writing a file to disk is a
 * high-risk action (spec 4.4) — it is the one command here that leaves the
 * browser — so `start` goes through the confirm hook.
 */
export async function downloadsStart(
  params: Record<string, unknown>,
): Promise<{ download: number }> {
  requireApi("downloads", "downloading files");
  const url = params.url;
  if (typeof url !== "string" || url === "") {
    throw new CommandError("invalid argument", "url must be a non-empty string");
  }
  const filename = params.filename;
  if (filename !== undefined && typeof filename !== "string") {
    throw new CommandError("invalid argument", "filename must be a string");
  }
  await confirm({ action: "download", method: "lg:downloads.start", url });

  const id = await chrome.downloads.download({
    url,
    ...(filename === undefined ? {} : { filename }),
    ...(params.saveAs === undefined ? {} : { saveAs: params.saveAs === true }),
  });
  return { download: id };
}

export async function downloadsList(
  params: Record<string, unknown>,
): Promise<{ downloads: chrome.downloads.DownloadItem[] }> {
  requireApi("downloads", "listing downloads");
  const { id, state, urlRegex, limit } = params;
  const downloads = await chrome.downloads.search({
    ...(typeof id === "number" ? { id } : {}),
    ...(typeof state === "string" ? { state: state as chrome.downloads.DownloadState } : {}),
    ...(typeof urlRegex === "string" ? { urlRegex } : {}),
    ...(typeof limit === "number" ? { limit } : {}),
  });
  return { downloads };
}

/** Cancels an in-progress download. A finished one cannot be cancelled. */
export async function downloadsCancel(
  params: Record<string, unknown>,
): Promise<{ cancelled: number }> {
  requireApi("downloads", "cancelling downloads");
  const id = params.id;
  if (typeof id !== "number") {
    throw new CommandError("invalid argument", "id must be the numeric download id");
  }
  await chrome.downloads.cancel(id);
  return { cancelled: id };
}
