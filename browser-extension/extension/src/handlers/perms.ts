import { CommandError } from "../protocol.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.permissions` + `optional_host_permissions`：把高危 host 权限从
 * 「安装即警告」改成「用时再申请」。request 只能在用户手势里成功——CLI 发来的
 * 指令没有手势，会得到浏览器的拒绝；真正要申请时从插件面板/设置页发起。
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

export async function permissionsRequest(params: Record<string, unknown>): Promise<{ granted: boolean }> {
  requireApi("permissions", "requesting permissions");
  // 需要用户手势；没有手势时 Chrome 直接返回 false，不会抛
  return { granted: await chrome.permissions.request(parsePerms(params)) };
}

export async function permissionsRemove(params: Record<string, unknown>): Promise<{ removed: boolean }> {
  requireApi("permissions", "removing permissions");
  return { removed: await chrome.permissions.remove(parsePerms(params)) };
}
