import { CommandError, optionalString, requireString } from "../protocol.ts";
import { requireApi } from "./context.ts";

/** `chrome.readingList`：Chrome 自带的「稍后阅读」列表的增删改查。 */

export async function readingListList(
  params: Record<string, unknown>,
): Promise<{ entries: chrome.readingList.ReadingListEntry[] }> {
  requireApi("readingList", "listing the reading list");
  const url = optionalString(params.url, "url");
  return { entries: await chrome.readingList.query(url === undefined ? {} : { url }) };
}

export async function readingListAdd(
  params: Record<string, unknown>,
): Promise<chrome.readingList.ReadingListEntry> {
  requireApi("readingList", "adding to the reading list");
  const url = requireString(params.url, "url");
  const title = requireString(params.title, "title");
  return chrome.readingList.add({
    url,
    title,
    ...(params.hasBeenRead === undefined ? {} : { hasBeenRead: params.hasBeenRead === true }),
  });
}

export async function readingListUpdate(
  params: Record<string, unknown>,
): Promise<unknown> {
  requireApi("readingList", "updating the reading list");
  const id = params.id;
  if (typeof id !== "number") {
    throw new CommandError("invalid argument", "id must be the numeric reading list entry id");
  }
  const url = optionalString(params.url, "url");
  const title = optionalString(params.title, "title");
  if (url === undefined && title === undefined && params.hasBeenRead === undefined) {
    throw new CommandError("invalid argument", "give at least one of url / title / hasBeenRead");
  }
  return chrome.readingList.update({
    id,
    ...(url === undefined ? {} : { url }),
    ...(title === undefined ? {} : { title }),
    ...(params.hasBeenRead === undefined ? {} : { hasBeenRead: params.hasBeenRead === true }),
  });
}

export async function readingListRemove(
  params: Record<string, unknown>,
): Promise<{ removed: number }> {
  requireApi("readingList", "removing from the reading list");
  const id = params.id;
  if (typeof id !== "number") {
    throw new CommandError("invalid argument", "id must be the numeric reading list entry id");
  }
  await chrome.readingList.remove({ id });
  return { removed: id };
}
