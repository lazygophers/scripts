import type { Event } from "./protocol.ts";

/**
 * Handlers that push (only `network.*` today) need a way out to the daemon,
 * but importing `NativeConnection` from a handler would make the module graph
 * circular. So background.ts hands the sink in at startup.
 */
let sink: ((event: Event) => void) | null = null;

export function setEventSink(fn: ((event: Event) => void) | null): void {
  sink = fn;
}

export function emitEvent(method: string, params: Record<string, unknown>): void {
  sink?.({ type: "event", method, params });
}
