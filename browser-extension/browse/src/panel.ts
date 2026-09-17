/**
 * The toolbar panel, spec 4.5. Three things:
 *
 * - the last commands and how they ended (live log),
 * - a brake that drops the native port right now,
 * - the domains that `confirm_mode: per_domain` has stopped asking about,
 *   each revocable.
 *
 * 三样东西现在都不经 daemon：免确认名单在 `chrome.storage.local` 里（面板直接读写），
 * 日志和刹车本来就是 service worker 自己的。所以本地程序没起来，这个面板照样能用。
 */
import { localize, msg } from "./i18n.ts";
import type { LogEntry } from "./native-port.ts";
import { getConfig, revoke } from "./policy.ts";

const list = document.getElementById("list") as HTMLUListElement | null;
const statusNode = document.getElementById("status");
const logList = document.getElementById("log") as HTMLUListElement | null;
const logStatus = document.getElementById("logStatus");
const bridgeList = document.getElementById("bridge") as HTMLUListElement | null;
const bridgeStatus = document.getElementById("bridgeStatus");
const serverLogList = document.getElementById("serverLog") as HTMLUListElement | null;
const serverLogStatus = document.getElementById("serverLogStatus");
const logFilter = document.getElementById("logFilter") as HTMLInputElement | null;
const logCopy = document.getElementById("logCopy") as HTMLButtonElement | null;
const cut = document.getElementById("cut") as HTMLButtonElement | null;
const settings = document.getElementById("settings") as HTMLButtonElement | null;

/**
 * 免确认名单直接读写 `chrome.storage.local`。以前这里要经 service worker 转给 daemon，
 * 因为名单存在 `browse.yaml` 里；现在它就在插件自己的存储里，中间那两跳全没了 ——
 * daemon 没起来也能撤销。
 */
async function call(op: string, domain?: string): Promise<string[]> {
  if (op === "revoke" && domain) {
    return (await revoke(domain)).approved_domains;
  }
  return (await getConfig()).approved_domains;
}

function render(domains: string[]): void {
  if (!list) {
    return;
  }
  list.replaceChildren();
  if (statusNode) {
    statusNode.textContent = domains.length
      ? msg("panelDomainCount", String(domains.length))
      : msg("panelNoDomains");
  }
  for (const domain of domains) {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.className = "domain";
    name.textContent = domain;
    const button = document.createElement("button");
    button.className = "btn";
    button.textContent = msg("panelRevoke");
    button.addEventListener("click", () => {
      button.disabled = true;
      void run(() => call("revoke", domain));
    });
    row.append(name, button);
    list.append(row);
  }
}

async function run(body: () => Promise<string[]>): Promise<void> {
  try {
    render(await body());
  } catch (err) {
    if (statusNode) {
      statusNode.textContent = err instanceof Error ? err.message : String(err);
    }
  }
}

/** 本轮拿到的指令日志。筛选框只改显示，不重新去问 service worker。 */
let entries: LogEntry[] = [];

/** service worker 上次报的连接状态。筛选时重画要用它，不能凭空当作已连上。 */
let lastState = "";

/** `14:03:07`。日志只活在这一次弹窗里，日期没有意义。 */
function clock(at: number): string {
  return new Date(at).toTimeString().slice(0, 8);
}

function renderLog(state: string = lastState): void {
  lastState = state;
  if (logStatus) {
    logStatus.textContent = msg(
      state === "connected" ? "panelDaemonConnected" : "panelDaemonDisconnected",
    );
  }
  if (!logList) {
    return;
  }
  const needle = (logFilter?.value ?? "").toLowerCase();
  const shown = needle
    ? entries.filter((entry) =>
        `${entry.method} ${entry.error}`.toLowerCase().includes(needle))
    : entries;
  logList.replaceChildren();
  if (shown.length === 0) {
    const row = document.createElement("li");
    row.textContent = msg("panelNothingRun");
    logList.append(row);
    return;
  }
  for (const entry of shown) {
    const row = document.createElement("li");
    row.className = "logline";
    const mark = document.createElement("span");
    mark.className = entry.ok ? "mark ok" : "mark bad";
    mark.textContent = entry.ok ? "✓" : "✗";
    const at = document.createElement("span");
    at.className = "at";
    at.textContent = clock(entry.at);
    const name = document.createElement("span");
    name.textContent = entry.ok ? entry.method : `${entry.method} — ${entry.error}`;
    const ms = document.createElement("span");
    ms.className = "ms";
    ms.textContent = `${entry.ms}ms`;
    row.append(mark, at, name, ms);
    logList.append(row);
  }
}

/** 一行一条，直接可粘进工单。复制的仍然只有方法名和成败，没有载荷。 */
function logText(): string {
  return entries
    .map((entry) =>
      `${clock(entry.at)} ${entry.ok ? "ok" : "fail"} ${entry.method} ${entry.ms}ms` +
      (entry.error ? ` ${entry.error}` : ""))
    .join("\n");
}

async function loadLog(): Promise<void> {
  try {
    const reply = await chrome.runtime.sendMessage({ type: "browse-log" });
    entries = (reply?.entries ?? []) as LogEntry[];
    renderLog(String(reply?.state ?? ""));
  } catch (err) {
    if (logStatus) {
      logStatus.textContent = err instanceof Error ? err.message : String(err);
    }
  }
}

/** 一行 `键：值`。bridge 的情况和日志都用它摆。 */
function line(label: string, value: string): HTMLLIElement {
  const row = document.createElement("li");
  row.className = "logline";
  const name = document.createElement("span");
  name.textContent = label;
  const detail = document.createElement("span");
  detail.className = "ms";
  detail.textContent = value;
  row.append(name, detail);
  return row;
}

/** 多久以前。只给个量级，面板上不需要精确到秒以下。 */
function ago(at: number): string {
  if (!at) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.round(seconds / 60)}min`;
}

/**
 * bridge 服务情况：上半截是扩展自己知道的（连没连上、重连第几次），下半截是问
 * bridge 要来的（pid、端口、跑了多久、连着谁）。bridge 没连上时只剩上半截，那正是
 * 这时候唯一有用的信息。
 */
function renderBridge(reply: Record<string, unknown>): void {
  const status = (reply.status ?? {}) as {
    state?: string; url?: string; attempt?: number; stopped?: boolean;
    connectedAt?: number; lastMessageAt?: number; reason?: string;
  };
  if (bridgeStatus) {
    bridgeStatus.textContent = status.stopped
      ? msg("panelBridgeStopped")
      : status.state === "connected"
        ? msg("panelBridgeUp", String(status.url ?? ""), ago(status.connectedAt ?? 0))
        : (status.attempt ?? 0) > 0
          ? msg("panelBridgeRetrying", String(status.attempt))
          : msg("panelBridgeDown", String(status.reason || "—"));
  }
  if (!bridgeList) {
    return;
  }
  bridgeList.replaceChildren();
  bridgeList.append(line(msg("panelBridgeLastSeen"), ago(status.lastMessageAt ?? 0)));

  const info = reply.info as {
    pid?: number; port?: number; uptimeSeconds?: number; logPath?: string;
    connections?: { browser: string; idleSeconds: number }[];
  } | undefined;
  if (info === undefined) {
    bridgeList.append(line(
      msg("panelBridgeService"),
      msg("panelBridgeUnreachable", String(reply.error ?? "—")),
    ));
    return;
  }
  bridgeList.append(line(msg("panelBridgeProcess"), `pid ${info.pid} · :${info.port}`));
  bridgeList.append(line(msg("panelBridgeUptime"), `${info.uptimeSeconds}s`));
  for (const conn of info.connections ?? []) {
    bridgeList.append(line(conn.browser, `${conn.idleSeconds}s`));
  }
  bridgeList.append(line(msg("panelBridgeLogPath"), String(info.logPath ?? "")));
}

/** bridge 服务端日志：一行一条事件，新的在下面（和文件里的顺序一致）。 */
function renderServerLog(lines: Record<string, unknown>[] | undefined): void {
  if (serverLogStatus) {
    serverLogStatus.textContent = lines && lines.length
      ? msg("panelDomainCount", String(lines.length))
      : msg("panelNoServerLog");
  }
  if (!serverLogList) {
    return;
  }
  serverLogList.replaceChildren();
  for (const entry of lines ?? []) {
    const at = typeof entry.at === "number" ? clock(entry.at * 1000) : "";
    const rest = Object.entries(entry)
      .filter(([key]) => key !== "at" && key !== "event")
      .map(([key, value]) => `${key}=${String(value)}`)
      .join(" ");
    const row = document.createElement("li");
    row.className = "logline";
    const stamp = document.createElement("span");
    stamp.className = "at";
    stamp.textContent = at;
    const name = document.createElement("span");
    name.textContent = String(entry.event ?? "");
    const detail = document.createElement("span");
    detail.className = "ms";
    detail.textContent = rest;
    row.append(stamp, name, detail);
    serverLogList.append(row);
  }
}

async function loadBridge(): Promise<void> {
  try {
    const reply = await chrome.runtime.sendMessage({ type: "browse-bridge", limit: 30 });
    renderBridge((reply ?? {}) as Record<string, unknown>);
    renderServerLog(reply?.lines as Record<string, unknown>[] | undefined);
  } catch (err) {
    if (bridgeStatus) {
      bridgeStatus.textContent = err instanceof Error ? err.message : String(err);
    }
  }
}

logFilter?.addEventListener("input", () => renderLog());

logCopy?.addEventListener("click", () => {
  void navigator.clipboard.writeText(logText()).then(() => {
    logCopy.textContent = msg("panelCopied");
  });
});

cut?.addEventListener("click", () => {
  cut.disabled = true;
  void chrome.runtime.sendMessage({ type: "browse-disconnect" }).then(() => {
    cut.textContent = msg("panelDisconnected");
    void loadLog();
    void loadBridge();
  });
});

// The options page is otherwise three clicks deep in chrome://extensions.
settings?.addEventListener("click", () => chrome.runtime.openOptionsPage());

localize();
void run(() => call("list"));
void loadLog();
void loadBridge();
