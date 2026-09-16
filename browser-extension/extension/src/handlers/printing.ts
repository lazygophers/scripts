import { CommandError, optionalString, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.printing` / `printingMetrics` / `printerProvider`。前两个是查询+提交
 * 打印任务；printerProvider 反着来——把 Chrome 打印对话框里的活转给扩展，这里
 * 转成 `lg:printing.request` 事件，CLI 用 `lg:printing.respond` 回状态。
 * 三个 API 都只在 ChromeOS 上存在，别的平台 requireApi 直接拒绝。
 */

const STATUSES = ["OK", "INVALID_TICKET", "INVALID_DATA", "FAILED"] as const;

/** base64 → ArrayBuffer（打印内容走它进 Blob；TS 5.7 的 BlobPart 不认 Uint8Array 泛型）。 */
function decodeBase64(value: string): ArrayBuffer {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

export async function printingPrinters(): Promise<{ printers: unknown[] }> {
  requireApi("printing", "listing printers");
  return { printers: await chrome.printing.getPrinters() };
}

export async function printingJobs(): Promise<{ jobs: unknown[] }> {
  requireApi("printing.getJobs", "listing print jobs");
  return { jobs: await chrome.printing.getJobs() };
}

export async function printingSubmit(
  params: Record<string, unknown>,
): Promise<{ job: string }> {
  requireApi("printing", "submitting print jobs");
  const printerId = requireString(params.printer, "printer", ", a printer id from lg:printing.printers");
  const title = requireString(params.title, "title");
  const contentBase64 = requireString(params.contentBase64, "contentBase64", ", base64 document bytes");
  const contentType = optionalString(params.contentType, "contentType") ?? "application/pdf";
  const content = new Blob([decodeBase64(contentBase64)], { type: contentType });
  // 旧版 @types 的 PrintJob 没有 content、submitJob 没有回包形状；按运行时形状调
  const submit = chrome.printing.submitJob as (request: { job: unknown }) => Promise<{
    jobId: string;
  }>;
  const job = await submit({
    job: { printerId, title, contentType, content },
  });
  return { job: job.jobId };
}

export async function printingCancelJob(
  params: Record<string, unknown>,
): Promise<{ cancelled: string }> {
  requireApi("printing", "cancelling print jobs");
  const job = requireString(params.job, "job", ", a job id from lg:printing.jobs");
  await chrome.printing.cancelJob(job);
  return { cancelled: job };
}

export async function printingMetrics(): Promise<{ jobs: unknown[] }> {
  requireApi("printingMetrics", "reading print job history");
  return { jobs: await chrome.printingMetrics.getPrintJobs() };
}

const printResponders = new Map<string, (status: string) => void>();

/** 打印请求转发给订阅方（background 启动时接线）。 */
export function listenPrinting(): void {
  chrome.printerProvider?.onPrintRequested?.addListener((printJob, callback) => {
    const requestId = `print-${printResponders.size + 1}-${Date.now()}`;
    printResponders.set(requestId, callback);
    emitEvent("lg:printing.request", {
      request: requestId,
      printer: printJob.printerId,
      ticket: printJob.ticket,
    });
  });
}

/** CLI 回答一条转发来的打印请求。 */
export async function printingRespond(
  params: Record<string, unknown>,
): Promise<{ responded: string }> {
  requireApi("printerProvider", "answering print requests");
  const request = requireString(params.request, "request", ", from the lg:printing.request event");
  const status = requireString(params.status, "status");
  if (!(STATUSES as readonly string[]).includes(status)) {
    throw new CommandError("invalid argument", `status must be one of ${STATUSES.join(", ")}`);
  }
  const callback = printResponders.get(request);
  if (!callback) {
    throw new CommandError("invalid argument", `no pending print request ${request}`);
  }
  printResponders.delete(request);
  callback(status);
  return { responded: request };
}
