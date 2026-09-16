import { CommandError, optionalString, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { requireApi } from "./context.ts";

/** `chrome.notifications` + `chrome.power`：两个「设备面」的小动作。 */

export async function notificationsShow(
  params: Record<string, unknown>,
): Promise<{ id: string }> {
  requireApi("notifications", "showing notifications");
  const title = requireString(params.title, "title");
  const message = requireString(params.message, "message");
  const iconUrl =
    optionalString(params.iconUrl, "iconUrl") ?? chrome.runtime.getURL("assets/icon.png");
  // 旧版 @types 把 create/clear 标成 void；运行时回 id / 回布尔
  const create = chrome.notifications.create as unknown as (
    options: chrome.notifications.NotificationOptions,
  ) => Promise<string>;
  const id = await create({
    type: "basic",
    iconUrl,
    title,
    message,
  });
  return { id };
}

export async function notificationsClear(
  params: Record<string, unknown>,
): Promise<{ cleared: boolean }> {
  requireApi("notifications", "clearing notifications");
  const id = requireString(params.id, "id", ", the id from lg:notifications.show");
  const clear = chrome.notifications.clear as (id: string) => Promise<boolean>;
  return { cleared: await clear(id) };
}

const LEVELS = ["system", "display"] as const;

/** `lg:power.keepAwake`：长时间自动化跑批时请求系统不休眠。 */
export async function powerKeepAwake(params: Record<string, unknown>): Promise<{ level: string }> {
  requireApi("power", "requesting keep-awake");
  const level = optionalString(params.level, "level") ?? "system";
  if (!(LEVELS as readonly string[]).includes(level)) {
    throw new CommandError("invalid argument", `level must be one of ${LEVELS.join(", ")}`);
  }
  chrome.power.requestKeepAwake(level as chrome.power.Level);
  return { level };
}

export async function powerRelease(): Promise<{ released: true }> {
  requireApi("power", "releasing keep-awake");
  chrome.power.releaseKeepAwake();
  return { released: true };
}

/** 点击通知转发给订阅方（background 启动时接线）。 */
export function listenNotifications(): void {
  chrome.notifications?.onClicked.addListener((id) => {
    emitEvent("lg:notifications.clicked", { id });
  });
}
