import { blobToBase64 } from "../base64.ts";
import { CommandError, optionalString, requireString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi, resolveContextOnce, targetUrl } from "./context.ts";

/**
 * 页面/屏幕捕获：MHTML 存档（pageCapture）、标签页录屏（tabCapture + offscreen
 * 的 DISPLAY_MEDIA）、屏幕/窗口录屏（desktopCapture，经 picker 小窗拿用户手势）。
 *
 * 录屏只有一份硬件流可拿：SW 没有 DOM，取流和 MediaRecorder 都住在同一个
 * offscreen 文档里（`offscreen-doc.ts`），SW 只发消息、管状态。
 */

/** 一次只允许一路录制（offscreen 文档里也就一个 MediaRecorder）。 */
interface Recording {
  id: string;
}
let active: Recording | null = null;
let seq = 0;

/**
 * 确保 offscreen 文档在。三个理由一把声明（CLIPBOARD / DISPLAY_MEDIA /
 * USER_MEDIA）：createDocument 对已存在的文档会抛，同一份文档重复用。
 */
export async function ensureOffscreen(justification: string): Promise<void> {
  requireApi("offscreen", "offscreen documents");
  const contexts = await chrome.runtime.getContexts({});
  const exists = contexts.some((context) => context.contextType === "OFFSCREEN_DOCUMENT");
  if (!exists) {
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: [
        chrome.offscreen.Reason.CLIPBOARD,
        chrome.offscreen.Reason.DISPLAY_MEDIA,
        chrome.offscreen.Reason.USER_MEDIA,
      ],
      justification,
    });
  }
}

/** `lg:pageCapture.saveMhtml`：整页存 MHTML。默认落成下载文件；save=false 回 base64。 */
export async function pageCaptureSaveMhtml(
  params: Record<string, unknown>,
): Promise<unknown> {
  requireApi("pageCapture", "saving a page as MHTML");
  const target = await resolveContextOnce(params);
  const url = await targetUrl(target);
  await confirm({ action: "readPage", method: "lg:pageCapture.saveMhtml", url });
  const blob = await chrome.pageCapture.saveAsMHTML({ tabId: target.tabId });
  if (blob === undefined) {
    throw new CommandError("unknown error", "saveAsMHTML returned nothing");
  }
  const base64 = await blobToBase64(blob);
  if (params.save === false) {
    return { base64, bytes: blob.size };
  }
  const filename = optionalString(params.filename, "filename") ?? `page-${target.tabId}.mhtml`;
  try {
    // SW 里没有 URL.createObjectURL，大文件走 data: URL；太大被拒就退回 base64
    const download = await chrome.downloads.download({
      url: `data:message/rfc822;base64,${base64}`,
      filename,
    });
    return { download, bytes: blob.size, filename };
  } catch {
    return { base64, bytes: blob.size, saved: false };
  }
}

/** `lg:offscreen.documents`：现在挂着哪些 offscreen 文档（状态查询）。 */
export async function offscreenDocuments(): Promise<{ documents: unknown[] }> {
  requireApi("runtime.getContexts", "listing extension contexts");
  const contexts = await chrome.runtime.getContexts({});
  return {
    documents: contexts
      .filter((context) => context.contextType === "OFFSCREEN_DOCUMENT")
      .map((context) => ({ documentUrl: context.documentUrl })),
  };
}

async function startRecording(
  kind: "tab" | "desktop",
  streamId: string,
): Promise<string> {
  if (active !== null) {
    throw new CommandError("invalid argument", `already recording (${active.id}); stop it first`);
  }
  await ensureOffscreen("recording a tab or the desktop, and clipboard access");
  seq += 1;
  const id = `rec-${seq}`;
  const reply = (await chrome.runtime.sendMessage({
    type: "lg:record-start",
    id,
    kind,
    streamId,
  })) as { ok?: boolean; error?: string } | undefined;
  if (!reply?.ok) {
    throw new CommandError("unknown error", reply?.error ?? "recording failed to start");
  }
  active = { id };
  return id;
}

/** `lg:capture.recordTab`：录当前/指定标签页的音视频。 */
export async function captureRecordTab(
  params: Record<string, unknown>,
): Promise<{ recording: string; tab: number }> {
  requireApi("tabCapture", "recording a tab");
  const target = await resolveContextOnce(params);
  const url = await targetUrl(target);
  await confirm({ action: "captureMedia", method: "lg:capture.recordTab", url });
  // 旧版 @types 里 getMediaStreamId 只有回调形态；运行时（Chrome 116+）返回 Promise
  const streamId = await (chrome.tabCapture.getMediaStreamId as (options: {
    targetTabId: number;
  }) => Promise<string>)({ targetTabId: target.tabId });
  const id = await startRecording("tab", streamId);
  return { recording: id, tab: target.tabId };
}

const SOURCES = ["screen", "window", "tab"] as const;
let pickerResolve: ((streamId: string | null) => void) | null = null;
let pickerWindow: number | undefined;

/**
 * `lg:capture.recordDesktop`：弹一个扩展自己的小窗让用户选屏幕/窗口——
 * chooseDesktopMedia 必须在扩展页的用户手势里调，CLI 指令本身没有手势。
 * 指令等到用户选完（或超时）才返回。
 */
export async function captureRecordDesktop(
  params: Record<string, unknown>,
): Promise<unknown> {
  requireApi("desktopCapture", "recording the screen or a window");
  await confirm({ action: "captureMedia", method: "lg:capture.recordDesktop", url: null });
  let sources: string[];
  if (params.sources === undefined) {
    sources = ["screen", "window"];
  } else if (
    Array.isArray(params.sources) &&
    params.sources.every((s) => (SOURCES as readonly string[]).includes(s as string)) &&
    params.sources.length > 0
  ) {
    sources = params.sources as string[];
  } else {
    throw new CommandError("invalid argument", `sources must be a list from ${SOURCES.join(", ")}`);
  }
  const timeoutSec = params.timeout;
  if (timeoutSec !== undefined && (typeof timeoutSec !== "number" || timeoutSec < 5 || timeoutSec > 600)) {
    throw new CommandError("invalid argument", "timeout must be a number of seconds in [5, 600]");
  }
  if (pickerResolve !== null) {
    throw new CommandError("invalid argument", "a source picker is already open");
  }
  const query = new URLSearchParams({ sources: sources.join(",") });
  const created = await chrome.windows.create({
    url: chrome.runtime.getURL(`picker.html?${query.toString()}`),
    type: "popup",
    width: 420,
    height: 260,
    focused: true,
  });
  pickerWindow = created?.id;
  const streamId = await new Promise<string | null>((resolve) => {
    const timer = setTimeout(() => resolve(null), (timeoutSec ?? 120) * 1000);
    pickerResolve = (id) => {
      clearTimeout(timer);
      resolve(id);
    };
  });
  pickerResolve = null;
  if (pickerWindow !== undefined) {
    void chrome.windows.remove(pickerWindow).catch(() => {});
    pickerWindow = undefined;
  }
  if (streamId === null) {
    throw new CommandError("lg:user rejected", "no desktop source was picked in time");
  }
  const id = await startRecording("desktop", streamId);
  return { recording: id };
}

/** picker 小窗回传选中的流（background 转发 `browse-pick` 消息到这里）。 */
export function desktopSourcePicked(streamId: unknown): boolean {
  if (pickerResolve === null) {
    return false;
  }
  pickerResolve(typeof streamId === "string" ? streamId : null);
  return true;
}

/** `lg:capture.recordStop`：收流、封 webm。默认存成下载文件，save=false 回 base64。 */
export async function captureRecordStop(
  params: Record<string, unknown>,
): Promise<unknown> {
  const recording = requireString(params.recording, "recording", ", the id from recordTab/recordDesktop");
  // SW 被杀重启后 active 会失忆，但录像在 offscreen 文档里还活着——
  // 在不在录以那边为准，这里照发 stop；active 只是缓存，用来挡 id 对不上的停止请求
  if (active !== null && recording !== active.id) {
    throw new CommandError("invalid argument", `not the active recording (${active.id})`);
  }
  const reply = (await chrome.runtime.sendMessage({
    type: "lg:record-stop",
    id: recording,
  })) as {
    ok?: boolean;
    error?: string;
    base64?: string;
    bytes?: number;
    kind?: "tab" | "desktop";
    seconds?: number;
  } | undefined;
  if (!reply?.ok) {
    throw new CommandError("unknown error", reply?.error ?? "recording failed to stop");
  }
  active = null;
  const { kind, seconds } = reply;
  if (params.save === false) {
    return { base64: reply.base64, bytes: reply.bytes, seconds, kind };
  }
  const filename =
    optionalString(params.filename, "filename") ?? `${recording}-${kind}.webm`;
  try {
    const download = await chrome.downloads.download({
      url: `data:video/webm;base64,${reply.base64}`,
      filename,
    });
    return { download, bytes: reply.bytes, seconds, kind, filename };
  } catch {
    return { base64: reply.base64, bytes: reply.bytes, seconds, kind, saved: false };
  }
}
