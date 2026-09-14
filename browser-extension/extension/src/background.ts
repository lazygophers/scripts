import { setEventSink } from "./events.ts";
import { NativeConnection, type ConnectionState } from "./native-port.ts";

/** Badge, spec 4.5: connected shows a dot, running commands show their count. */
let state: ConnectionState = "disconnected";
let inFlight = 0;

function paintBadge(): void {
  const text = inFlight > 0 ? String(inFlight) : state === "connected" ? "●" : "";
  const color = inFlight > 0 ? "#d97706" : "#16a34a";
  void chrome.action.setBadgeBackgroundColor({ color });
  void chrome.action.setBadgeText({ text });
  void chrome.action.setTitle({
    title:
      state === "connected"
        ? `browse: connected${inFlight > 0 ? `, ${inFlight} running` : ""}`
        : "browse: daemon not connected",
  });
}

const connection = new NativeConnection(
  (next) => {
    state = next;
    paintBadge();
  },
  (count) => {
    inFlight = count;
    paintBadge();
  },
);

// `network.*` pushes events; handlers cannot import the connection without a
// cycle, so it is handed in here.
setEventSink((event) => connection.send(event));

// TODO(T08): setConfirmHook(...) belongs here too — the confirm policy
// (`confirm_mode`, the per-domain allow list, the audit log) lives in the
// daemon, so the hook has to round-trip over `connection`. Until T08 lands the
// default hook allows everything, which is `confirm_mode: silent`.

// A service worker restart (install, browser start, idle eviction) re-runs this
// file, so connecting at module scope is the whole lifecycle handling needed.
paintBadge();
connection.connect();

chrome.runtime.onStartup.addListener(() => connection.connect());
chrome.runtime.onInstalled.addListener(() => connection.connect());
