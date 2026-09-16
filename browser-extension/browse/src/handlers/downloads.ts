import { CommandError, optionalString, requireString } from "../protocol.ts";
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
  const url = requireString(params.url, "url");
  const filename = optionalString(params.filename, "filename");
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
    ...(typeof state === "string" ? { state: state as chrome.downloads.DownloadItem["state"] } : {}),
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

/** `lg:downloads.open`：用系统默认程序打开一个下载产物（downloads.open 权限）。 */
export async function downloadsOpen(
  params: Record<string, unknown>,
): Promise<{ opened: number }> {
  requireApi("downloads.open", "opening downloaded files");
  const id = params.id;
  if (typeof id !== "number") {
    throw new CommandError("invalid argument", "id must be the numeric download id");
  }
  await chrome.downloads.open(id);
  return { opened: id };
}
