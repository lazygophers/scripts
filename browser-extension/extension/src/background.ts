import { NativeConnection, type ConnectionState } from "./native-port.js";

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

// A service worker restart (install, browser start, idle eviction) re-runs this
// file, so connecting at module scope is the whole lifecycle handling needed.
paintBadge();
connection.connect();

chrome.runtime.onStartup.addListener(() => connection.connect());
chrome.runtime.onInstalled.addListener(() => connection.connect());
