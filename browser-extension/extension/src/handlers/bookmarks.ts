import { optionalString, requireString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi } from "./context.ts";

/** `lg:bookmarks.search`. Reading bookmarks is a high-risk action (spec 4.4). */
export async function bookmarksSearch(
  params: Record<string, unknown>,
): Promise<{ nodes: chrome.bookmarks.BookmarkTreeNode[] }> {
  requireApi("bookmarks", "reading bookmarks");
  await confirm({ action: "readBookmarks", method: "lg:bookmarks.search", url: null });

  const { url, title } = params;
  const query = optionalString(params.query, "query");
  const nodes =
    query !== undefined
      ? await chrome.bookmarks.search(query)
      : await chrome.bookmarks.search({
          ...(typeof url === "string" ? { url } : {}),
          ...(typeof title === "string" ? { title } : {}),
        });
  return { nodes };
}

/** `lg:bookmarks.create`. Omit `url` to create a folder. */
export async function bookmarksCreate(
  params: Record<string, unknown>,
): Promise<{ node: chrome.bookmarks.BookmarkTreeNode }> {
  requireApi("bookmarks", "creating bookmarks");
  const { parentId, index } = params;
  const title = optionalString(params.title, "title");
  const url = optionalString(params.url, "url");
  await confirm({
    action: "writeBookmarks",
    method: "lg:bookmarks.create",
    url: url ?? null,
  });

  const node = await chrome.bookmarks.create({
    ...(url === undefined ? {} : { url }),
    ...(title === undefined ? {} : { title }),
    ...(typeof parentId === "string" ? { parentId } : {}),
    ...(typeof index === "number" ? { index } : {}),
  });
  return { node };
}

/**
 * `lg:bookmarks.remove`. A folder needs `recursive: true`, because
 * `bookmarks.remove` refuses a non-empty folder and the alternative
 * (`removeTree`) would delete children the caller never named.
 */
export async function bookmarksRemove(
  params: Record<string, unknown>,
): Promise<{ removed: string }> {
  requireApi("bookmarks", "removing bookmarks");
  const id = requireString(params.id, "id", ", a bookmark id");
  await confirm({ action: "writeBookmarks", method: "lg:bookmarks.remove", url: null });

  if (params.recursive === true) {
    await chrome.bookmarks.removeTree(id);
  } else {
    await chrome.bookmarks.remove(id);
  }
  return { removed: id };
}
