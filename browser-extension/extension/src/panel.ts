/**
 * The toolbar panel, spec 4.5. Three things:
 *
 * - the last commands and how they ended (live log),
 * - a brake that drops the native port right now,
 * - the domains that `confirm_mode: per_domain` has stopped asking about,
 *   each revocable.
 *
 * The allow list lives in `browse.yaml` on the Python side, so that part goes
 * through the service worker to the daemon. The log and the brake are the
 * service worker's own — they keep working when the daemon does not.
 */
import { localize, msg } from "./i18n.ts";
import type { LogEntry } from "./native-port.ts";

const list = document.getElementById("list") as HTMLUListElement | null;
const statusNode = document.getElementById("status");
const logList = document.getElementById("log") as HTMLUListElement | null;
const logStatus = document.getElementById("logStatus");
const cut = document.getElementById("cut") as HTMLButtonElement | null;
const settings = document.getElementById("settings") as HTMLButtonElement | null;

async function call(op: string, domain?: string): Promise<string[]> {
  const reply = await chrome.runtime.sendMessage({ type: "browse-approvals", op, domain });
  if (!reply?.ok) {
    throw new Error(reply?.error ?? msg("panelNoAnswer"));
  }
  return reply.domains as string[];
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
