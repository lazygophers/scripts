import { answerConfirm, askUser, windowClosed } from "./confirm-ui.ts";
import { setEventSink } from "./events.ts";
import { setConfirmHook } from "./handlers/confirm.ts";
import { NativeConnection, type ConnectionState } from "./native-port.ts";

/** Panel op → daemon method. The daemon owns the list; this is just naming. */
const APPROVALS: Record<string, string> = {
  list: "lg:approvals.list",
  approve: "lg:approvals.approve",
  revoke: "lg:approvals.revoke",
};

interface Approvals {
  domains: string[];
}

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

// The confirm policy (`confirm_mode`, the per-domain allow list, the deny
// list, the audit log) is the daemon's. What lives here is only the UI: the
// daemon sends `lg:confirm.request`, `confirmRequest` routes it through
// `confirm()`, and this hook is what actually faces the user.
setConfirmHook(askUser);

// Answers from the dialog window and queries from the panel. Both are
// extension pages talking to the service worker, never to the daemon directly.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "browse-confirm") {
    sendResponse({ ok: answerConfirm(message.token, message.approved) });
    return false;
  }
  if (message?.type !== "browse-approvals") {
    return false;
  }
  const method = APPROVALS[message.op as string];
  if (!method) {
    sendResponse({ ok: false, error: `unknown approvals op: ${message.op}` });
    return false;
  }
  connection
    .request(method, message.domain ? { domain: message.domain } : {})
    .then(
      (result) => sendResponse({ ok: true, domains: (result as Approvals).domains }),
      (err: Error) => sendResponse({ ok: false, error: err.message }),
    );
  return true; // answered asynchronously
});

// Closing the dialog without choosing is a refusal — the daemon must not be
// left waiting out its full timeout for a window that no longer exists.
chrome.windows?.onRemoved.addListener(windowClosed);

// A service worker restart (install, browser start, idle eviction) re-runs this
// file, so connecting at module scope is the whole lifecycle handling needed.
paintBadge();
connection.connect();

chrome.runtime.onStartup.addListener(() => connection.connect());
chrome.runtime.onInstalled.addListener(() => connection.connect());
