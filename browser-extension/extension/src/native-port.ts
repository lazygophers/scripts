import { nextDelay } from "./backoff.ts";
import { dispatch } from "./handlers/index.ts";
import {
  CommandError,
  NATIVE_HOST,
  isCommand,
  type ErrorCode,
  type ErrorReply,
  type Outbound,
  type Success,
} from "./protocol.ts";

/**
 * Keeps one `connectNative` port to the daemon open, reconnecting with
 * backoff whenever it drops (spec 3.4), and routes inbound Commands through
 * the handler table.
 *
 * The periodic ping is not a health check for the daemon: an MV3 service
 * worker is killed after 30s idle, and port traffic is what resets that timer.
 * Doing it over the existing port avoids asking for the `alarms` permission,
 * which spec 4.3 does not include.
 */
const PING_INTERVAL_MS = 20_000;

/** How many commands the panel's live log keeps (spec 4.5). */
const LOG_SIZE = 20;

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

export class NativeConnection {
  private port: chrome.runtime.Port | null = null;
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
    if (this.port) {
      return;
    }
    let port: chrome.runtime.Port;
    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (err) {
      // Host manifest missing entirely: same treatment as a dropped port.
      this.scheduleReconnect(describe(err));
      return;
    }
    this.port = port;
    port.onMessage.addListener((msg: unknown) => {
      // The first message proves the host really started; connectNative
      // succeeds even when the host binary is missing.
      this.attempt = 0;
      void this.handle(msg);
    });
    port.onDisconnect.addListener(() => {
      this.port = null;
      this.scheduleReconnect(describe(chrome.runtime.lastError));
    });
    this.onState("connected");
    this.startPing();
    console.info(`[browse] connected to ${NATIVE_HOST}`);
  }

  /** Panel / CLI brake: drop the port and stop reconnecting (spec 4.5). */
  disconnect(): void {
    this.stopped = true;
    this.clearReconnect();
    this.stopPing();
    this.port?.disconnect();
    this.port = null;
    this.onState("disconnected");
  }

  send(message: Outbound): void {
    this.port?.postMessage(message);
  }

  /**
   * Ask the daemon something and wait for its reply. Only `lg:approvals.*`
   * uses this — the panel reading and editing the per-domain allow list
   * (spec 4.5). The *policy* stays in Python; this carries the question.
   */
  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const port = this.port;
    if (!port) {
      return Promise.reject(new Error("daemon not connected"));
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
      port.postMessage({ id, method, params });
    });
  }

  private async handle(msg: unknown): Promise<void> {
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
      `[browse] daemon unavailable (${reason}); retry #${this.attempt} in ${delay}ms`,
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
