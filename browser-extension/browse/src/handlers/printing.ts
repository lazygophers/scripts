import { CommandError, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.printerProvider`：把 Chrome 打印对话框里的活转给扩展，这里转成
 * `lg:printing.request` 事件，CLI 用 `lg:printing.respond` 回状态。
 * `chrome.printing` / `printingMetrics` 只在 ChromeOS 上存在，在别的平台永远
 * 是 unsupported operation，所以那一面（printers/jobs/submit/cancelJob/metrics）
 * 已经删掉，不留假入口。
 */

const STATUSES = ["OK", "INVALID_TICKET", "INVALID_DATA", "FAILED"] as const;

/** 打印结果只能是上面那四种，回调的类型也就跟着收窄了。 */
type PrintStatus = (typeof STATUSES)[number];

const printResponders = new Map<string, (status: PrintStatus) => void>();

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
  callback(status as PrintStatus);
  return { responded: request };
}
