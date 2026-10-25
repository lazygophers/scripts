import { answerConfirm, askUser, windowClosed } from "./confirm-ui.ts";
import { setEventSink } from "./events.ts";
import { setConfirmHook } from "./handlers/confirm.ts";
import { invalidateTab } from "./handlers/cache.ts";
import { consoleForget, consoleNavigate, consoleReset } from "./handlers/console.ts";
import { netRuleArm, netRuleForget, netRuleReset } from "./handlers/netrule.ts";
import { desktopSourcePicked, pickerClosed } from "./handlers/capture.ts";
import { listenGcm } from "./handlers/gcm.ts";
import { listenNotifications } from "./handlers/notify.ts";
import { listenPrinting } from "./handlers/printing.ts";
import { userScriptsDisconnect, userScriptsReconnect } from "./handlers/scripts.ts";
import { listenUi } from "./handlers/ui.ts";
import { listenWauth } from "./handlers/wauth.ts";
import { NativeConnection, browserName, instanceId, type ConnectionState } from "./native-port.ts";
import { log, setTelemetrySink } from "./telemetry.ts";

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
    if (next === "disconnected") { // bridge 断了，debugger 会话全摘
      consoleReset();
      netRuleReset();
      void userScriptsDisconnect(); // reset 清注册；重连由 userScriptsReconnect 按持久化表恢复
    }
    if (next === "connected") {
      void userScriptsReconnect(); // 按持久化表恢复断连前注册的 userScripts
    }
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

// 遥测走统一 JSONL：sink 在这里注入（native-port↔telemetry 不成环）。桥没连上时
// request() 立刻拒绝，telemetry 保留队列，重连后 onopen 的 flush 补发。
setTelemetrySink(async (entries) => {
  await connection.request("lg:extension.log", {
    browser: browserName(),
    instanceId: await instanceId(),
    entries,
  });
});

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
    void bridgeReport().then(sendResponse);
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
 * bridge 的服务情况 + 本浏览器自己的连接。日志原文不再出 service worker（用户：
 * 插件不要直接展示日志的内容），bridge 没连上时照样返回连接近况——那正是用户
 * 这时候最需要看的东西，所以缺了不算失败。
 */
async function bridgeReport(): Promise<Record<string, unknown>> {
  const status = connection.status();
  try {
    const info = await connection.request("lg:bridge.info");
    // 面板用它把别的浏览器的槽位滤掉，只摆自己的那行
    const ownInstanceId = await instanceId();
    return { ok: true, status, info, ownInstanceId };
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
// picker 小窗同理：关掉就是没选，立刻放行等待中的 recordDesktop。
chrome.windows?.onRemoved.addListener((windowId) => {
  windowClosed(windowId);
  pickerClosed(windowId);
});

// 页面缓存（票 05）：导航/刷新（status=loading）清该页，tab 关了整页清。
// console 采集同步：导航清环（采集继续），关 tab 摘会话。
chrome.tabs?.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading") {
    void invalidateTab(tabId);
    consoleNavigate(tabId);
  }
});
chrome.tabs?.onRemoved.addListener((tabId) => {
  void invalidateTab(tabId);
  consoleForget(tabId);
  netRuleForget(tabId);
});

// A service worker restart (install, browser start, idle eviction) re-runs this
// file, so connecting at module scope is the whole lifecycle handling needed.
paintBadge();
log("sw-start", "info", { attempt: 0 }); // 全新 SW 还没有重试，attempt 恒 0；跨 SW 存活靠 storage 队列
connection.connect();
// 服务重启后把持久化的 net 路由重新武装（attach 不能跨重启存活）
void netRuleArm();

chrome.runtime.onStartup.addListener(() => {
  // 浏览器完整启动是「停止」状态的唯一自动解除点（GLOSSARY.md）；连不上由
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
