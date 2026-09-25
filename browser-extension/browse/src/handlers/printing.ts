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

interface Pending {
  callback: (status: PrintStatus) => void;
  timer: ReturnType<typeof setTimeout>;
}

const printResponders = new Map<string, Pending>();

/**
 * 序号只增不减。用过 `printResponders.size + 1`：size 会随 respond 回落，同一毫秒里
 * 「A 进 → B 进 → 答 A → C 进」时 C 会拿到和 B 一样的 id 并把 B 顶掉，B 的 callback
 * 就永远丢了。
 */
let printSeq = 0;

/**
 * 没人回答的打印请求多久自动判失败。
 *
 * 另一端是人：CLI 把 `lg:printing.request` 事件递给使用者，由他决定回 OK 还是拒绝，
 * 所以不能按机器的反应时间来掐。五分钟足够一个人看完并回答，又短到 CLI 断线 / 崩掉
 * 时不会让 Chrome 的打印对话框一直挂着——callback 不调用，那个对话框不会自己结束。
 */
export const PRINT_TIMEOUT_MS = 5 * 60 * 1000;

/** 回答一条请求：清掉定时器、移出 map、把状态交回 Chrome。只会生效一次。 */
function settle(requestId: string, status: PrintStatus): boolean {
  const pending = printResponders.get(requestId);
  if (!pending) {
    return false;
  }
  clearTimeout(pending.timer);
  printResponders.delete(requestId);
  pending.callback(status);
  return true;
}

/** 打印请求转发给订阅方（background 启动时接线）。 */
export function listenPrinting(): void {
  chrome.printerProvider?.onPrintRequested?.addListener((printJob, callback) => {
    printSeq += 1;
    const requestId = `print-${printSeq}-${Date.now()}`;
    const timer = setTimeout(() => void settle(requestId, "FAILED"), PRINT_TIMEOUT_MS);
    printResponders.set(requestId, { callback, timer });
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
  if (!settle(request, status as PrintStatus)) {
    throw new CommandError("invalid argument", `no pending print request ${request}`);
  }
  return { responded: request };
}
