import { requireString } from "../protocol.ts";
import { ensureOffscreen } from "./capture.ts";
import { confirm } from "./confirm.ts";

/**
 * 剪贴板：service worker 没有 DOM，`navigator.clipboard` 要在文档上下文里用，
 * 所以读写都转发给 offscreen 文档（理由 CLIPBOARD），那边才是真正动手的人。
 */

export async function clipboardRead(): Promise<{ text: string }> {
  // 剪贴板里往往是密码管理器刚复制的密码或 2FA 码，所以读是高危动作（spec 4.4，
  // policy.ts 的 RISKY_METHODS 也这么记）。写没有对应动作：内容是调用方自己给的。
  await confirm({ action: "readClipboard", method: "lg:clipboard.read", url: null });
  await ensureOffscreen("clipboard access");
  const reply = (await chrome.runtime.sendMessage({ type: "lg:clipboard", op: "read" })) as
    | { ok?: boolean; text?: string; error?: string }
    | undefined;
  if (!reply?.ok) {
    throw new Error(reply?.error ?? "clipboard read failed");
  }
  return { text: reply.text ?? "" };
}

export async function clipboardWrite(
  params: Record<string, unknown>,
): Promise<{ wrote: number }> {
  const text = requireString(params.text, "text");
  await ensureOffscreen("clipboard access");
  const reply = (await chrome.runtime.sendMessage({ type: "lg:clipboard", op: "write", text })) as
    | { ok?: boolean; error?: string }
    | undefined;
  if (!reply?.ok) {
    throw new Error(reply?.error ?? "clipboard write failed");
  }
  return { wrote: text.length };
}
