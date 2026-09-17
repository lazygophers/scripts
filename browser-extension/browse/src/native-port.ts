import { nextDelay } from "./backoff.ts";
import { dispatch } from "./handlers/index.ts";
import {
  CommandError,
  isCommand,
  type ErrorCode,
  type ErrorReply,
  type Outbound,
  type Success,
} from "./protocol.ts";

/**
 * 2026-09-15 起扩展直连本地 bridge 的 WebSocket（用户批准的连接层重做）。
 * 以前是 native messaging：浏览器 fork 一个 host 进程，stdin/stdout 中转 —— 三层
 * 生命周期互相不同步，Chrome/Arc 共享 manifest 还会互踢。现在只剩一条连接：
 *
 *     扩展 SW ──WebSocket 127.0.0.1:9330──▶ browse bridge ──unix socket──▶ CLI
 *
 * 协议帧还是原来的 JSON 信封（command/success/error/event + hello），只是不再带
 * 4 字节长度前缀 —— 一条 WS 消息就是一帧，bridge 那头适配（`lib/browse_bridge.py`）。
 *
 * 心跳仍走 `lg:keepalive.ping`（每 20 秒一条）：bridge 靠它刷新连接活跃时间，
 * service worker 靠**发送消息本身**重置 MV3 的 30 秒闲置计时器（Chrome 116 起，
 * manifest 的 minimum_chrome_version 已经钉在 116）。断线退避重连（`backoff.ts`），
 * 另有 `background.ts` 的 30 秒 alarm 兜底把被杀的 worker 叫醒。
 */

/** bridge 的 WS 地址。端口是双方约定死的常量（`lib/browse_bridge.py: DEFAULT_WS_PORT）。 */
export const BRIDGE_URL = "ws://127.0.0.1:9330";

const PING_INTERVAL_MS = 20_000;

/** How many commands the panel's live log keeps (spec 4.5). */
const LOG_SIZE = 20;

/**
 * 每台安装持久的实例 ID（`chrome.storage.local`，首次生成后不变）。`browserName()`
 * 只是展示名，Arc/Chromium 分支识别不出品牌时会退化成同一个 "chromium" —— 这个
 * ID 才是 bridge 用来分辨「这是不是同一个扩展实例重连」的凭据（`lib/browse_daemon.py`
 * `_browser_slot`），两台都报 "chromium" 时不再互踢，而是各开一个槽位。
 *
 * `chrome.storage` 在测试环境里不存在，读不到就退化成一个不持久的随机 ID —— 单次
 * 连接仍然唯一，只是重连后会变，测试不关心这个字段所以无妨。
 */
let cachedInstanceId: string | null = null;
async function instanceId(): Promise<string> {
  if (cachedInstanceId !== null) {
    return cachedInstanceId;
  }
  const key = "browse:instanceId";
  try {
    const stored = await chrome.storage.local.get(key);
    const existing = stored[key];
    if (typeof existing === "string" && existing !== "") {
      cachedInstanceId = existing;
    } else {
      const id = crypto.randomUUID();
      await chrome.storage.local.set({ [key]: id });
      cachedInstanceId = id;
    }
  } catch {
    cachedInstanceId = crypto.randomUUID();
  }
  return cachedInstanceId;
}

/** hello 里报的浏览器名。展示用 —— 路由不靠它猜，靠 bridge 的 connectionId + instanceId。 */
function browserName(): string {
  const brands = (navigator as { userAgentData?: { brands?: { brand: string }[] } })
    .userAgentData?.brands ?? [];
  for (const { brand } of brands) {
    if (brand === "Google Chrome") {
      return "chrome";
    }
    if (brand === "Microsoft Edge") {
      return "edge";
    }
    const lower = brand.toLowerCase();
    if (["brave", "opera", "vivaldi", "arc"].includes(lower)) {
      return lower;
    }
  }
  return "chromium"; // Arc 等 Chromium 分支不在 brands 里报自己 —— 展示名退化，路由不受影响
}

export type ConnectionState = "connected" | "disconnected";

/** One line of the panel log. Method and outcome only — never the payload. */
export interface LogEntry {
  at: number;
  method: string;
  ok: boolean;
  ms: number;
  /** Error code on failure, empty on success. */
  error: string;
}

/** 面板显示的连接近况。`state` 之外的几项都是为了回答「为什么连不上」。 */
export type ConnectionStatus = {
  state: ConnectionState;
  url: string;
  /** 已经重试了几次；0 表示没在重连。 */
  attempt: number;
  /** 用户按过「立即断开」：此后不自动重连。 */
  stopped: boolean;
  /** 连上的时刻（毫秒）；没连上是 0。 */
  connectedAt: number;
  /** 最后一次收到 bridge 消息的时刻（毫秒）。心跳每 20 秒一次，所以它也是「还活着」的凭据。 */
  lastMessageAt: number;
  /** 上一次断开的原因。 */
  reason: string;
};

export class NativeConnection {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private inFlight = 0;
  private stopped = false;
  // Our own outbound commands (`lg:approvals.*` for the panel). Ids are ours;
  // they never collide with the daemon's because each side only ever matches
  // replies against the ids *it* issued.
  private outSeq = 0;
  private outbound = new Map<number, (reply: Success | ErrorReply) => void>();
  // Newest last. The payload never enters it — same rule as the audit log
  // (spec 4.6): what was done, not what was read.
  private log: LogEntry[] = [];
  // 面板要显示的那几个数：连上的时刻、最后一次收到消息的时刻、上次断开的原因。
  private connectedAt = 0;
  private lastMessageAt = 0;
  private lastReason = "";

  // Assigned in the body, not as constructor parameter properties: Node's
  // type-stripping (`node --test` on .ts sources) rejects those, same as
  // `CommandError` in protocol.ts.
  private readonly onState: (state: ConnectionState) => void;
  private readonly onInFlight: (count: number) => void;

  constructor(
    onState: (state: ConnectionState) => void,
    onInFlight: (count: number) => void,
  ) {
    this.onState = onState;
    this.onInFlight = onInFlight;
  }

  connect(): void {
    this.stopped = false;
    this.clearReconnect();
    if (this.socket) {
      return;
    }
    let socket: WebSocket;
    try {
      socket = new WebSocket(BRIDGE_URL);
    } catch (err) {
      // bridge 不在/网络栈拒绝：和断线同一个处理，退避重试
      this.scheduleReconnect(describe(err));
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      void instanceId().then((id) => {
        if (this.socket !== socket) {
          return; // 等 ID 的这一下 socket 已经被 disconnect() 或重连换掉了
        }
        socket.send(JSON.stringify({
          type: "hello",
          role: "extension",
          browser: browserName(),
          instanceId: id,
        }));
      });
      this.connectedAt = Date.now();
      this.lastReason = "";
      this.onState("connected");
      this.startPing();
    };
    socket.onmessage = (event) => {
      // 第一条消息（含 hello-ack）证明 bridge 真的活着且认了我们
      this.attempt = 0;
      this.lastMessageAt = Date.now();
      if (typeof event.data !== "string") {
        return;
      }
      try {
        void this.handle(JSON.parse(event.data));
      } catch {
        console.warn("[browse] dropped non-JSON message");
      }
    };
    const dropped = () => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.scheduleReconnect("bridge 连接断开");
    };
    socket.onclose = dropped;
    socket.onerror = dropped;
  }

  /** Panel / CLI brake: drop the socket and stop reconnecting (spec 4.5). */
  disconnect(): void {
    this.stopped = true;
    this.clearReconnect();
    this.stopPing();
    this.socket?.close();
    this.socket = null;
    this.onState("disconnected");
  }

  send(message: Outbound): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  /**
   * Ask the bridge something and wait for its reply. Only `lg:approvals.*`
   * uses this — the panel reading and editing the per-domain allow list
   * (spec 4.5). The *policy* stays in the extension (`policy.ts`); the bridge
   * only carries the question, same as every other command.
   */
  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const socket = this.socket;
    if (socket?.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("bridge not connected"));
    }
    this.outSeq += 1;
    const id = this.outSeq;
    return new Promise((resolve, reject) => {
      this.outbound.set(id, (reply) => {
        if (reply.type === "success") {
          resolve(reply.result);
        } else {
          reject(new CommandError(reply.error, reply.message));
        }
      });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }

  private async handle(msg: unknown): Promise<void> {
    if ((msg as { type?: string })?.type === "hello-ack") {
      return; // 握手应答，不是指令也不是回包
    }
    const reply = asReply(msg);
    if (reply) {
      this.outbound.get(reply.id)?.(reply);
      this.outbound.delete(reply.id);
      return;
    }
    if (!isCommand(msg)) {
      console.warn("[browse] dropped non-command message", msg);
      return;
    }
    this.inFlight += 1;
    this.onInFlight(this.inFlight);
    const started = Date.now();
    try {
      const result = await dispatch(msg.method, msg.params ?? {});
      this.send({ type: "success", id: msg.id, result });
      this.record(msg.method, started, "");
    } catch (err) {
      const [error, message] = classify(err);
      this.send({ type: "error", id: msg.id, error, message });
      this.record(msg.method, started, error);
    } finally {
      this.inFlight -= 1;
      this.onInFlight(this.inFlight);
    }
  }

  /**
   * 连接自己的近况，面板用来回答「现在通不通、上次为什么断」。
   *
   * 这些字段本来只活在重连逻辑里（只写进 console），出问题时用户看不到；面板要显示
   * bridge 状态，第一手就是这几个数。
   */
  status(): ConnectionStatus {
    return {
      state: this.socket?.readyState === WebSocket.OPEN ? "connected" : "disconnected",
      url: BRIDGE_URL,
      attempt: this.attempt,
      stopped: this.stopped,
      connectedAt: this.connectedAt,
      lastMessageAt: this.lastMessageAt,
      reason: this.lastReason,
    };
  }

  /** The panel's live log, newest first (spec 4.5). */
  recent(): LogEntry[] {
    return [...this.log].reverse();
  }

  private record(method: string, started: number, error: string): void {
    this.log.push({ at: started, method, ok: error === "", ms: Date.now() - started, error });
    if (this.log.length > LOG_SIZE) {
      this.log.shift();
    }
  }

  private scheduleReconnect(reason: string): void {
    this.lastReason = reason;
    this.connectedAt = 0;
    this.stopPing();
    this.onState("disconnected");
    // Nobody is left to answer these; leaving them pending hangs the panel.
    for (const [id, settle] of this.outbound) {
      settle({ type: "error", id, error: "lg:browser not connected", message: reason });
    }
    this.outbound.clear();
    if (this.stopped || this.reconnectTimer !== null) {
      return;
    }
    const delay = nextDelay(this.attempt);
    this.attempt += 1;
    console.info(
      `[browse] bridge unavailable (${reason}); retry #${this.attempt} in ${delay}ms`,
    );
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      this.send({ type: "event", method: "lg:keepalive.ping", params: {} });
    }, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}

/** A reply to one of *our* commands. Daemon-issued Commands carry a `method`. */
function asReply(msg: unknown): Success | ErrorReply | null {
  const m = msg as { id?: unknown; type?: unknown } | null;
  if (typeof m !== "object" || m === null || typeof m.id !== "number") {
    return null;
  }
  return m.type === "success" || m.type === "error" ? (m as Success | ErrorReply) : null;
}

function classify(err: unknown): [ErrorCode, string] {
  if (err instanceof CommandError) {
    return [err.code, err.message];
  }
  return ["unknown error", describe(err)];
}

function describe(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  if (err && typeof err === "object" && "message" in err) {
    return String((err as { message: unknown }).message);
  }
  return String(err ?? "port closed");
}
