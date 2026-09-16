import { CommandError } from "../protocol.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.permissions` 的只读面：查当前授予了什么。manifest 没有
 * `optional_permissions` 段，Chrome 规定只能申请预先列过的权限，所以
 * request/remove 没有语义（remove 还是单向门），不暴露。
 */

function parsePerms(params: Record<string, unknown>): chrome.permissions.Permissions {
  const permissions = params.permissions;
  const origins = params.origins;
  if (
    permissions !== undefined &&
    (!Array.isArray(permissions) || permissions.some((p) => typeof p !== "string"))
  ) {
    throw new CommandError("invalid argument", "permissions must be a list of strings");
  }
  if (origins !== undefined && (!Array.isArray(origins) || origins.some((o) => typeof o !== "string"))) {
    throw new CommandError("invalid argument", "origins must be a list of strings");
  }
  const asked = {
    ...(Array.isArray(permissions) ? { permissions } : {}),
    ...(Array.isArray(origins) ? { origins } : {}),
  };
  if (Object.keys(asked).length === 0) {
    throw new CommandError("invalid argument", "give permissions and/or origins");
  }
  return asked as chrome.permissions.Permissions;
}

export async function permissionsGetAll(): Promise<unknown> {
  requireApi("permissions", "querying granted permissions");
  return chrome.permissions.getAll();
}

export async function permissionsContains(params: Record<string, unknown>): Promise<{ contains: boolean }> {
  requireApi("permissions", "querying granted permissions");
  return { contains: await chrome.permissions.contains(parsePerms(params)) };
}
