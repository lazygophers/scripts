/**
 * 设置页（spec 4.4 / 4.6）。
 *
 * **配置就存在这里 —— `chrome.storage.local`，插件自己的存储。** 2026-09-14 之前它经
 * daemon 读写 `browse.yaml`，于是本地程序没起来就什么都改不了；用户要的正是这个：
 * 「这些都应该是浏览器自己做的」。现在这个页面不和 daemon 说一句话，**daemon 开没开
 * 都能读能改能存**。
 *
 * 裁决的人也是插件自己（`src/policy.ts`），所以这里存下去的东西下一条指令立刻生效，
 * 不存在「改了但那边还没重新加载」的窗口。
 */
import { localize, msg } from "./i18n.ts";
import {
  CONFIG_KEY,
  CONFIRM_MODES,
  FEATURES,
  readConfig,
  revoke as revokeDomain,
  setConfig,
  type BrowseConfig,
} from "./policy.ts";

export { CONFIRM_MODES, FEATURES, type BrowseConfig };

/**
 * Check a draft before sending it. The daemon validates again — it is the
 * boundary and cannot trust this page — but catching it here is what turns a
 * round trip into an instant message next to the field.
 */
export function validate(draft: Partial<BrowseConfig>): string {
  if (draft.confirm_mode !== undefined
      && !CONFIRM_MODES.includes(draft.confirm_mode as (typeof CONFIRM_MODES)[number])) {
    return msg("settingsBadMode", draft.confirm_mode);
  }
  if (draft.audit_retention_days !== undefined && !Number.isInteger(draft.audit_retention_days)) {
    return msg("settingsBadRetention");
  }
  return "";
}

/** Textarea / 单行文本 → domain list. Blank entries and stray whitespace drop out. */
export function parseDomains(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split(/[\s,;]+/)) {
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

/** 配置在哪：给用户看的那一行。不是文件路径了，是插件自己的存储。 */
function whereItLives(): string {
  return `chrome.storage.local · ${CONFIG_KEY}`;
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
  renderFeatures(config);
}

/**
 * 功能目录的展示和开关（spec 4.4 的延伸）。目录本身在 `policy.FEATURES`，这里只画：
 * 一行一个功能，勾选框管全局禁用，旁边一格填「只对这些域名禁用」。
 */
function renderFeatures(config: BrowseConfig): void {
  const box = byId("features");
  if (!box) {
    return;
  }
  const perDomain: Record<string, string[]> = {};
  for (const [domain, ids] of Object.entries(config.domain_disabled_features)) {
    for (const id of ids) {
      (perDomain[id] ??= []).push(domain);
    }
  }
  box.replaceChildren();
  for (const feature of FEATURES) {
    const row = document.createElement("div");
    row.className = "feature";
    row.dataset.feature = feature.id;

    const label = document.createElement("label");
    const off = document.createElement("input");
    off.type = "checkbox";
    off.className = "feat-off";
    off.checked = config.disabled_features.includes(feature.id);
    label.append(off);
    const name = document.createElement("span");
    name.textContent = msg(`settingsFeat${feature.id.slice(0, 1).toUpperCase()}${feature.id.slice(1)}`);
    label.append(name);
    row.append(label);

    const methods = document.createElement("code");
    methods.textContent = feature.methods.join(" ");
    row.append(methods);

    const domains = document.createElement("input");
    domains.type = "text";
    domains.className = "feat-domains";
    domains.placeholder = msg("settingsFeatDomains");
    domains.spellcheck = false;
    domains.value = (perDomain[feature.id] ?? []).join(" ");
    row.append(domains);

    box.append(row);
  }
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
  try {
    renderApproved((await revokeDomain(domain)).approved_domains);
  } catch (err) {
    say(err instanceof Error ? err.message : String(err), "bad");
  }
}

/** Read the form back out. Only the fields this page owns. */
export function readForm(): Partial<BrowseConfig> {
  const checked = document.querySelector<HTMLInputElement>(
    'input[name="confirm_mode"]:checked',
  );
  const retention = byId<HTMLInputElement>("retention");
  // 功能开关：勾了「禁用」进全局名单；填了域名进按域名名单。目录顺序即返回顺序。
  const disabled_features: string[] = [];
  const domain_disabled_features: Record<string, string[]> = {};
  for (const feature of FEATURES) {
    const row = document.querySelector<HTMLDivElement>(`.feature[data-feature="${feature.id}"]`);
    if (!row) {
      continue;
    }
    if (row.querySelector<HTMLInputElement>(".feat-off")?.checked === true) {
      disabled_features.push(feature.id);
    }
    const text = row.querySelector<HTMLInputElement>(".feat-domains")?.value ?? "";
    for (const domain of parseDomains(text)) {
      (domain_disabled_features[domain] ??= []).push(feature.id);
    }
  }
  return {
    // 一个都没选中时给空串：`validate()` 会挡下它。**不能悄悄填成 silent** ——
    // 那是最松的模式，静静地把用户设成它是这条路上最不能犯的错。
    confirm_mode: (checked?.value ?? "") as BrowseConfig["confirm_mode"],
    deny_domains: parseDomains(byId<HTMLTextAreaElement>("deny")?.value ?? ""),
    audit: byId<HTMLInputElement>("audit")?.checked === true,
    audit_retention_days: Number.parseInt(retention?.value ?? "", 10),
    disabled_features,
    domain_disabled_features,
  };
}

async function load(): Promise<void> {
  try {
    render(await readConfig(), whereItLives());
    setOffline(false);
    say("");
  } catch (err) {
    // 走到这里说明插件自己的存储坏了，不是 daemon 的事 —— daemon 这个页面根本不碰
    setOffline(true);
    say(err instanceof Error ? err.message : String(err), "bad");
  }
}

async function save(): Promise<void> {
  const draft = readForm();
  const problem = validate(draft);
  if (problem) {
    say(problem, "bad");
    return;
  }
  say(msg("settingsSaving"));
  try {
    render(await setConfig(draft), whereItLives());
    setOffline(false);
    say(msg("settingsSaved"), "good");
  } catch (err) {
    say(err instanceof Error ? err.message : String(err), "bad");
  }
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
