import { nextDelay } from "./backoff.js";
import { dispatch } from "./handlers/index.js";
import {
  CommandError,
  NATIVE_HOST,
  isCommand,
  type ErrorCode,
  type Outbound,
} from "./protocol.js";

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

export type ConnectionState = "connected" | "disconnected";

export class NativeConnection {
  private port: chrome.runtime.Port | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private inFlight = 0;
  private stopped = false;

  constructor(
    private readonly onState: (state: ConnectionState) => void,
    private readonly onInFlight: (count: number) => void,
  ) {}

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

  private async handle(msg: unknown): Promise<void> {
    if (!isCommand(msg)) {
      console.warn("[browse] dropped non-command message", msg);
      return;
    }
    this.inFlight += 1;
    this.onInFlight(this.inFlight);
    try {
      const result = await dispatch(msg.method, msg.params ?? {});
      this.send({ type: "success", id: msg.id, result });
    } catch (err) {
      const [error, message] = classify(err);
      this.send({ type: "error", id: msg.id, error, message });
    } finally {
      this.inFlight -= 1;
      this.onInFlight(this.inFlight);
    }
  }

  private scheduleReconnect(reason: string): void {
    this.stopPing();
    this.onState("disconnected");
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
