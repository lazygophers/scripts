/**
 * The settings page, spec 4.4 / 4.6.
 *
 * **The policy lives in `browse.yaml`, not here.** This page reads and writes
 * that file through the daemon (`lg:config.get` / `lg:config.set`), the same
 * way the panel edits the per-domain allow list. Nothing is kept in
 * `chrome.storage`: a second copy of the policy would drift, and it would drift
 * towards "the user thinks it is off". That is also why the file's real path is
 * printed on the page — so it is obvious that this and the command line are
 * editing one thing.
 *
 * When the daemon is not reachable the page says so and disables the form. It
 * must never fall back to showing an empty config, which reads as "your
 * settings are gone".
 */
import { localize, msg } from "./i18n.ts";

/** The five fields of spec 4.4 / 4.6, as the daemon returns them. */
export interface BrowseConfig {
  confirm_mode: string;
  deny_domains: string[];
  approved_domains: string[];
  audit: boolean;
  audit_retention_days: number;
}

/** Mirrors `CONFIRM_MODES` in `lib/browse_security.py`, loosest first. */
export const CONFIRM_MODES = ["silent", "per_domain", "always"];

interface Reply {
  ok: boolean;
  config?: BrowseConfig;
  path?: string;
  domains?: string[];
  error?: string;
  state?: string;
}

/**
 * Check a draft before sending it. The daemon validates again — it is the
 * boundary and cannot trust this page — but catching it here is what turns a
 * round trip into an instant message next to the field.
 */
export function validate(draft: Partial<BrowseConfig>): string {
  if (draft.confirm_mode !== undefined && !CONFIRM_MODES.includes(draft.confirm_mode)) {
    return msg("settingsBadMode", draft.confirm_mode);
  }
  if (draft.audit_retention_days !== undefined && !Number.isInteger(draft.audit_retention_days)) {
    return msg("settingsBadRetention");
  }
  return "";
}

/** Textarea text → domain list. Blank lines and stray whitespace drop out. */
export function parseDomains(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split("\n")) {
    const domain = line.trim().replace(/^\*/, "").replace(/^\./, "").toLowerCase();
    if (domain && !out.includes(domain)) {
      out.push(domain);
    }
  }
  return out;
}

// ------------------------------------------------------------------ the page

const byId = <T extends HTMLElement>(id: string): T | null =>
  document.getElementById(id) as T | null;

function say(text: string, kind: "" | "good" | "bad" = ""): void {
  const node = byId("status");
  if (node) {
    node.textContent = text;
    node.className = kind;
  }
}

function setOffline(offline: boolean): void {
  document.body.classList.toggle("offline", offline);
}

async function ask(op: "get" | "set", config?: Partial<BrowseConfig>): Promise<Reply> {
  return (await chrome.runtime.sendMessage({ type: "browse-config", op, config })) as Reply;
}

export function render(config: BrowseConfig, path: string): void {
  const pathNode = byId("path");
  if (pathNode) {
    pathNode.textContent = path;
  }
  for (const input of Array.from(
    document.querySelectorAll<HTMLInputElement>('input[name="confirm_mode"]'),
  )) {
    input.checked = input.value === config.confirm_mode;
  }
  const deny = byId<HTMLTextAreaElement>("deny");
  if (deny) {
    deny.value = config.deny_domains.join("\n");
  }
  const audit = byId<HTMLInputElement>("audit");
  if (audit) {
    audit.checked = config.audit;
  }
  const retention = byId<HTMLInputElement>("retention");
  if (retention) {
    retention.value = String(config.audit_retention_days);
  }
  renderApproved(config.approved_domains);
}

function renderApproved(domains: string[]): void {
  const list = byId<HTMLUListElement>("approved");
  if (!list) {
    return;
  }
  list.replaceChildren();
  if (domains.length === 0) {
    const row = document.createElement("li");
    row.textContent = msg("settingsApprovedEmpty");
    list.append(row);
    return;
  }
  for (const domain of domains) {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.className = "domain";
    name.textContent = domain;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = msg("panelRevoke");
    button.addEventListener("click", () => {
      button.disabled = true;
      void revoke(domain);
    });
    row.append(name, button);
    list.append(row);
  }
}

async function revoke(domain: string): Promise<void> {
  // The panel's existing path, writing the same file — no second implementation.
  const reply = (await chrome.runtime.sendMessage({
    type: "browse-approvals",
    op: "revoke",
    domain,
  })) as Reply;
  if (reply?.ok) {
    renderApproved(reply.domains ?? []);
  } else {
    say(reply?.error ?? msg("panelNoAnswer"), "bad");
  }
}

/** Read the form back out. Only the fields this page owns. */
export function readForm(): Partial<BrowseConfig> {
  const checked = document.querySelector<HTMLInputElement>(
    'input[name="confirm_mode"]:checked',
  );
  const retention = byId<HTMLInputElement>("retention");
  return {
    confirm_mode: checked?.value ?? "",
    deny_domains: parseDomains(byId<HTMLTextAreaElement>("deny")?.value ?? ""),
    audit: byId<HTMLInputElement>("audit")?.checked === true,
    audit_retention_days: Number.parseInt(retention?.value ?? "", 10),
  };
}

async function load(): Promise<void> {
  const reply = await ask("get");
  if (!reply?.ok || !reply.config) {
    setOffline(true);
    say(reply?.error ?? msg("panelNoAnswer"), "bad");
    return;
  }
  setOffline(false);
  render(reply.config, reply.path ?? "");
  say("");
}

async function save(): Promise<void> {
  const draft = readForm();
  const problem = validate(draft);
  if (problem) {
    say(problem, "bad");
    return;
  }
  say(msg("settingsSaving"));
  const reply = await ask("set", draft);
  if (!reply?.ok || !reply.config) {
    setOffline(reply?.state !== "connected");
    say(reply?.error ?? msg("panelNoAnswer"), "bad");
    return;
  }
  render(reply.config, reply.path ?? "");
  say(msg("settingsSaved"), "good");
}

const form = byId<HTMLFormElement>("form");
if (form) {
  localize();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void save();
  });
  void load();
}
