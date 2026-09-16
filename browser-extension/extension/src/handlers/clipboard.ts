import { requireString } from "../protocol.ts";
import { ensureOffscreen } from "./capture.ts";

/**
 * 剪贴板：service worker 没有 DOM，`navigator.clipboard` 要在文档上下文里用，
 * 所以读写都转发给 offscreen 文档（理由 CLIPBOARD），那边才是真正动手的人。
 */

export async function clipboardRead(): Promise<{ text: string }> {
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
