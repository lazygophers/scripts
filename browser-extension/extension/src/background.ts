import { answerConfirm, askUser, windowClosed } from "./confirm-ui.ts";
import { setEventSink } from "./events.ts";
import { setConfirmHook } from "./handlers/confirm.ts";
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

// 2026-09-14 起策略（confirm_mode、免确认名单、拒绝名单、审计）全在插件自己这边，
// 存在 chrome.storage.local 里。handlers 在动手那一行调 `confirm()`，它查完策略决定要
// 不要问；这个 hook 就是「问」的那一步 —— 弹窗，面向用户。
setConfirmHook(askUser);

// 弹窗页回的答案，加上面板的实时日志和刹车。设置页和免确认名单不在这里 —— 它们直接
// 读写 chrome.storage.local，连 service worker 都不用经过。
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "browse-confirm") {
    sendResponse({ ok: answerConfirm(message.token, message.approved) });
    return false;
  }
  // The panel's live log and its brake (spec 4.5). Both are local to the
  // service worker — no daemon round trip, so the brake still works when the
  // daemon is the thing that has gone wrong.
  if (message?.type === "browse-log") {
    sendResponse({ ok: true, entries: connection.recent(), state });
    return false;
  }
  if (message?.type === "browse-disconnect") {
    connection.disconnect();
    sendResponse({ ok: true });
    return false;
  }
  return false;
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
