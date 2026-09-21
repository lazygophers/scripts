import { answerConfirm, askUser, windowClosed } from "./confirm-ui.ts";
import { setEventSink } from "./events.ts";
import { setConfirmHook } from "./handlers/confirm.ts";
import { desktopSourcePicked } from "./handlers/capture.ts";
import { listenGcm } from "./handlers/gcm.ts";
import { listenNotifications } from "./handlers/notify.ts";
import { listenPrinting } from "./handlers/printing.ts";
import { listenUi } from "./handlers/ui.ts";
import { listenWauth } from "./handlers/wauth.ts";
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
        : "browse: bridge not connected",
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
  // 面板要显示 bridge 的服务情况和日志。连接近况是 service worker 自己的；
  // bridge 那两条只读方法要走 WebSocket 问它，所以这一支是异步的。
  if (message?.type === "browse-bridge") {
    void bridgeReport(Number(message.limit) || 30).then(sendResponse);
    return true;
  }
  if (message?.type === "browse-disconnect") {
    connection.disconnect(); // 刹车状态由 NativeConnection 持久化，alarm 自然尊重它
    sendResponse({ ok: true });
    return false;
  }
  // desktopCapture 的选源小窗回传流 id（capture.ts 在等它）
  if (message?.type === "browse-pick") {
    sendResponse({ ok: desktopSourcePicked(message.streamId) });
    return false;
  }
  return false;
});

/**
 * bridge 的服务情况 + 服务端日志。bridge 没连上时照样返回连接近况——那正是用户
 * 这时候最需要看的东西，所以服务端那两项缺了不算失败。
 */
async function bridgeReport(limit: number): Promise<Record<string, unknown>> {
  const status = connection.status();
  try {
    const [info, log] = await Promise.all([
      connection.request("lg:bridge.info"),
      connection.request("lg:bridge.log", { limit }),
    ]);
    return { ok: true, status, info, lines: (log as { lines?: unknown[] }).lines ?? [] };
  } catch (err) {
    return { ok: false, status, error: err instanceof Error ? err.message : String(err) };
  }
}

// 2026-09-16 扩容的事件面：快捷键/地址栏/推送/通知点击/WebAuthn 请求/打印请求，
// 全部转发给订阅方（events.ts 的 sink）。
listenUi();
listenGcm();
listenNotifications();
listenWauth();
listenPrinting();

// Closing the dialog without choosing is a refusal — the daemon must not be
// left waiting out its full timeout for a window that no longer exists.
chrome.windows?.onRemoved.addListener(windowClosed);

// A service worker restart (install, browser start, idle eviction) re-runs this
// file, so connecting at module scope is the whole lifecycle handling needed.
paintBadge();
connection.connect();

chrome.runtime.onStartup.addListener(() => {
  // 浏览器完整启动是「停止」状态的唯一自动解除点（CONTEXT.md）；连不上由
  // connect() 自己的停止守卫挡住，alarm 同理。
  connection.resume();
});
chrome.runtime.onInstalled.addListener(() => connection.connect());

// 唤醒闹钟（2026-09-15，用户要求「装完/守护进程重启后不用重启浏览器」）。MV3 的
// service worker 空闲 30 秒就会被杀，被杀时重连定时器跟着死掉，之后没有任何事件
// 叫醒它 —— 连接就永远停在断开。30 秒一次的 alarm 是官方唯一的自唤醒途径；worker
// 一起来，模块顶部的 connect() 就会跑。用户刹车（NativeConnection 持久化的
// stopped）在 connect() 内部挡住重连，直到下一次浏览器启动。
chrome.alarms?.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms?.onAlarm.addListener((alarm) => {
  if (alarm.name === "reconnect") {
    connection.connect();
  }
});
