/**
 * offscreen 文档：SW 没有 DOM，录屏的 MediaRecorder 和剪贴板的
 * navigator.clipboard 都住在这里。SW 发消息进来，这边动手、把结果回在
 * sendMessage 的应答里（Blob 走不了 runtime 消息，回 base64）。
 *
 * 录制的真身在内存里 SW 是看不到的——id / kind / 时长都记在这边，stop 的
 * 应答回带回去，SW 那份只是缓存，被杀重启了也不影响停录。
 */
import { blobToBase64 } from "./base64.ts";

let recorder: MediaRecorder | null = null;
let chunks: Blob[] = [];
let recordingId = "";
let recordingKind: "tab" | "desktop" = "tab";
let startedAt = 0;

// getUserMedia 的 chromeMediaSource 约束不在 TS 的 DOM 类型里，扩一下。
interface TabOrDesktopConstraints {
  audio: { mandatory: { chromeMediaSource: string; chromeMediaSourceId: string } };
  video: {
    mandatory: {
      chromeMediaSource: string;
      chromeMediaSourceId: string;
      maxWidth: number;
      maxHeight: number;
    };
  };
}


chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  const msg = message as { type?: string } | null;
  if (msg?.type === "lg:record-start") {
    void start(message as { id: string; kind: "tab" | "desktop"; streamId: string }, sendResponse);
    return true; // 异步应答
  }
  if (msg?.type === "lg:record-stop") {
    stop(sendResponse);
    return true;
  }
  if (msg?.type === "lg:clipboard") {
    void clip(message as { op: "read" | "write"; text?: string }, sendResponse);
    return true;
  }
  return false;
});

async function start(
  message: { id: string; kind: "tab" | "desktop"; streamId: string },
  sendResponse: (reply: unknown) => void,
): Promise<void> {
  if (recorder !== null) {
    sendResponse({ ok: false, error: "offscreen document is already recording" });
    return;
  }
  const source = message.kind === "tab" ? "tab" : "desktop";
  const constraints = {
    audio: { mandatory: { chromeMediaSource: source, chromeMediaSourceId: message.streamId } },
    video: {
      mandatory: {
        chromeMediaSource: source,
        chromeMediaSourceId: message.streamId,
        maxWidth: 1920,
        maxHeight: 1080,
      },
    },
  } as unknown as MediaStreamConstraints;
  try {
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    chunks = [];
    recordingId = message.id;
    recordingKind = message.kind;
    startedAt = Date.now();
    recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        chunks.push(event.data);
      }
    };
    recorder.start(1000);
    sendResponse({ ok: true });
  } catch (err) {
    sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) });
  }
}

function stop(sendResponse: (reply: unknown) => void): void {
  if (recorder === null) {
    sendResponse({ ok: false, error: "not recording" });
    return;
  }
  const done = recorder;
  const id = recordingId;
  const kind = recordingKind;
  const started = startedAt;
  recorder = null;
  recordingId = "";
  done.onstop = () => {
    done.stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(chunks, { type: "video/webm" });
    void blobToBase64(blob).then((base64) => {
      sendResponse({
        ok: true,
        id,
        kind,
        seconds: (Date.now() - started) / 1000,
        base64,
        bytes: blob.size,
      });
    });
  };
  done.stop();
}

async function clip(
  message: { op: "read" | "write"; text?: string },
  sendResponse: (reply: unknown) => void,
): Promise<void> {
  try {
    if (message.op === "read") {
      sendResponse({ ok: true, text: await navigator.clipboard.readText() });
    } else {
      await navigator.clipboard.writeText(message.text ?? "");
      sendResponse({ ok: true });
    }
  } catch (err) {
    sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) });
  }
}
