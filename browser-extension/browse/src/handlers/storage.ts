import { CommandError, optionalString, requireString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi, resolveContextOnce, targetUrl } from "./context.ts";
import { runInPage, type PageResult } from "./inject.ts";

/**
 * `storage.getCookies` / `setCookie` / `deleteCookies` over `chrome.cookies`,
 * and `getLocalStorage` / `setLocalStorage` over an injected page script
 * (localStorage is per-origin and has no extension API).
 *
 * Reading cookies and reading localStorage are high-risk actions (spec 4.4):
 * they are session credentials. Both go through the confirm hook.
 */
export async function storageGetCookies(
  params: Record<string, unknown>,
): Promise<{ cookies: chrome.cookies.Cookie[] }> {
  requireApi("cookies", "reading cookies");
  const filter = cookieFilter(params);
  await confirm({
    action: "readCookies",
    method: "storage.getCookies",
    url: filter.url ?? filter.domain ?? null,
  });
  const cookies = await chrome.cookies.getAll(filter);
  return { cookies };
}

export async function storageSetCookie(
  params: Record<string, unknown>,
): Promise<{ cookie: chrome.cookies.Cookie | null }> {
  requireApi("cookies", "writing cookies");
  const url = requireString(params.url, "url");
  const name = requireString(params.name, "name");
  const value = requireString(params.value, "value");
  await confirm({ action: "writeCookies", method: "storage.setCookie", url });

  const cookie = await chrome.cookies.set({
    url,
    name,
    value,
    ...(typeof params.domain === "string" ? { domain: params.domain } : {}),
    ...(typeof params.path === "string" ? { path: params.path } : {}),
    ...(typeof params.expirationDate === "number"
      ? { expirationDate: params.expirationDate }
      : {}),
    ...(params.secure === undefined ? {} : { secure: params.secure === true }),
    ...(params.httpOnly === undefined ? {} : { httpOnly: params.httpOnly === true }),
  });
  if (cookie === null) {
    throw new CommandError("unknown error", `the browser refused to set cookie ${name}`);
  }
  return { cookie };
}

export async function storageDeleteCookies(
  params: Record<string, unknown>,
): Promise<{ deleted: number }> {
  requireApi("cookies", "deleting cookies");
  const filter = cookieFilter(params);
  await confirm({
    action: "writeCookies",
    method: "storage.deleteCookies",
    url: filter.url ?? filter.domain ?? null,
  });

  const cookies = await chrome.cookies.getAll(filter);
  for (const cookie of cookies) {
    const scheme = cookie.secure ? "https" : "http";
    await chrome.cookies.remove({
      url: `${scheme}://${cookie.domain.replace(/^\./, "")}${cookie.path}`,
      name: cookie.name,
      ...(cookie.storeId === undefined ? {} : { storeId: cookie.storeId }),
    });
  }
  return { deleted: cookies.length };
}

export async function storageGetLocalStorage(
  params: Record<string, unknown>,
): Promise<{ entries: Record<string, string> }> {
  const target = await resolveContextOnce(params);
  await confirm({
    action: "readLocalStorage",
    method: "storage.getLocalStorage",
    url: await targetUrl(target),
  });
  const key = optionalString(params.key, "key");
  const entries = await runInPage(target, "ISOLATED", pageReadLocalStorage, [key ?? null]);
  return { entries };
}

export async function storageSetLocalStorage(
  params: Record<string, unknown>,
): Promise<{ written: number }> {
  const entries = normalizeEntries(params);
  const target = await resolveContextOnce(params);
  await confirm({
    action: "writeLocalStorage",
    method: "storage.setLocalStorage",
    url: await targetUrl(target),
  });
  const written = await runInPage(target, "ISOLATED", pageWriteLocalStorage, [entries]);
  return { written };
}

/** Page side. Closes over nothing; see handlers/inject.ts. */
export function pageReadLocalStorage(
  key: string | null,
): PageResult<Record<string, string>> {
  try {
    const out: Record<string, string> = {};
    if (key !== null) {
      const value = localStorage.getItem(key);
      if (value !== null) out[key] = value;
      return { ok: true, value: out };
    }
    for (let i = 0; i < localStorage.length; i += 1) {
      const name = localStorage.key(i);
      if (name !== null) out[name] = localStorage.getItem(name) ?? "";
    }
    return { ok: true, value: out };
  } catch (err) {
    // A sandboxed or opaque-origin frame throws SecurityError on access.
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}

/** Page side. A null value removes the key. */
export function pageWriteLocalStorage(
  entries: Record<string, string | null>,
): PageResult<number> {
  try {
    let written = 0;
    for (const [name, value] of Object.entries(entries)) {
      if (value === null) localStorage.removeItem(name);
      else localStorage.setItem(name, value);
      written += 1;
    }
    return { ok: true, value: written };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}

function normalizeEntries(params: Record<string, unknown>): Record<string, string | null> {
  const { key, value, entries } = params;
  if (entries !== undefined) {
    if (typeof entries !== "object" || entries === null || Array.isArray(entries)) {
      throw new CommandError("invalid argument", "entries must be an object");
    }
    const out: Record<string, string | null> = {};
    for (const [name, raw] of Object.entries(entries as Record<string, unknown>)) {
      out[name] = raw === null ? null : String(raw);
    }
    return out;
  }
  const name = requireString(key, "key");
  return { [name]: value === null || value === undefined ? null : String(value) };
}

function cookieFilter(params: Record<string, unknown>): chrome.cookies.GetAllDetails {
  const { url, domain, name } = params;
  if (url === undefined && domain === undefined) {
    throw new CommandError("invalid argument", "pass url or domain to scope the cookies");
  }
  return {
    ...(typeof url === "string" ? { url } : {}),
    ...(typeof domain === "string" ? { domain } : {}),
    ...(typeof name === "string" ? { name } : {}),
  };
}
