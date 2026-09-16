/**
 * `lg:audit.read` / `lg:audit.clear` —— 把审计从插件里捞出来。
 *
 * 审计搬进 `chrome.storage.local` 之后，命令行再也读不到那个文件了。这两条是**唯一**
 * 的出口（`browse audit`），没有它们用户就只能在插件面板上一条条翻。
 *
 * 读自己的日志不算高危动作：里面已经没有任何内容（只有「对哪个域名做了什么」），而且
 * 想看日志的人本来就是装这个扩展的人。所以不过 `confirm()`。
 */
import { CommandError } from "../protocol.ts";
import { clear, read, type AuditEntry } from "../audit.ts";

export async function auditRead(
  params: Record<string, unknown>,
): Promise<{ entries: AuditEntry[] }> {
  const raw = params.limit;
  if (raw !== undefined && (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0)) {
    throw new CommandError("invalid argument", `limit 要是非负整数，收到 ${JSON.stringify(raw)}`);
  }
  return { entries: await read(raw as number | undefined) };
}

export async function auditClear(): Promise<{ cleared: number }> {
  return { cleared: await clear() };
}
