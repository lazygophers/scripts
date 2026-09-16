import { CommandError, optionalString, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.instanceID` / `chrome.gcm`：推送通道的 token 侧。拿 token 需要调用方
 * 自己提供 authorizedEntity（发件方的项目号），扩展这边只是转交。
 */

export async function gcmId(): Promise<{ id: string }> {
  requireApi("instanceID", "reading the instance id");
  return { id: await chrome.instanceID.getID() };
}

export async function gcmToken(params: Record<string, unknown>): Promise<{ token: string }> {
  requireApi("instanceID", "getting a push token");
  const entity = requireString(params.entity, "entity", ", the sender's project number");
  const scope = optionalString(params.scope, "scope") ?? "";
  return { token: await chrome.instanceID.getToken({ authorizedEntity: entity, scope }) };
}

export async function gcmDeleteToken(params: Record<string, unknown>): Promise<{ deleted: string }> {
  requireApi("instanceID", "deleting a push token");
  const entity = requireString(params.entity, "entity");
  const scope = optionalString(params.scope, "scope") ?? "";
  await chrome.instanceID.deleteToken({ authorizedEntity: entity, scope });
  return { deleted: entity };
}

/** 推送消息转发给订阅方（background 启动时接线）。 */
export function listenGcm(): void {
  chrome.gcm?.onMessage?.addListener((message) => {
    emitEvent("lg:gcm.message", {
      from: message.from,
      collapseKey: message.collapseKey ?? null,
      data: message.data ?? {},
    });
  });
}
