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

function renderLog(entries: LogEntry[], state: string): void {
  if (logStatus) {
    logStatus.textContent = msg(
      state === "connected" ? "panelDaemonConnected" : "panelDaemonDisconnected",
    );
  }
  if (!logList) {
    return;
  }
  logList.replaceChildren();
  if (entries.length === 0) {
    const row = document.createElement("li");
    row.textContent = msg("panelNothingRun");
    logList.append(row);
    return;
  }
  for (const entry of entries) {
    const row = document.createElement("li");
    const mark = document.createElement("span");
    mark.className = entry.ok ? "ok" : "bad";
    mark.textContent = entry.ok ? "✓" : "✗";
    const name = document.createElement("span");
    name.textContent = entry.ok ? entry.method : `${entry.method} — ${entry.error}`;
    const ms = document.createElement("span");
    ms.className = "ms";
    ms.textContent = `${entry.ms}ms`;
    row.append(mark, name, ms);
    logList.append(row);
  }
}

async function loadLog(): Promise<void> {
  try {
    const reply = await chrome.runtime.sendMessage({ type: "browse-log" });
    renderLog((reply?.entries ?? []) as LogEntry[], String(reply?.state ?? ""));
  } catch (err) {
    if (logStatus) {
      logStatus.textContent = err instanceof Error ? err.message : String(err);
    }
  }
}

cut?.addEventListener("click", () => {
  cut.disabled = true;
  void chrome.runtime.sendMessage({ type: "browse-disconnect" }).then(() => {
    cut.textContent = msg("panelDisconnected");
    void loadLog();
  });
});

// The options page is otherwise three clicks deep in chrome://extensions.
settings?.addEventListener("click", () => chrome.runtime.openOptionsPage());

localize();
void run(() => call("list"));
void loadLog();
