/**
 * 确认策略：模式、拒绝名单、免确认名单。**存储和裁决都在插件里。**
 *
 * 2026-09-14 的架构反转（用户原话「这些都应该是浏览器自己做的，cli 只负责调用、解析，
 * 具体的存储等等功能都作为插件的功能」）。在那之前策略住在 Python 侧的 `browse.yaml`，
 * 由 daemon 在转发前裁决。现在：
 *
 *     设置页 ──► chrome.storage.local   ← 唯一的配置真相，本地程序开没开都能改
 *     插件   ──► 自己裁决、自己记审计
 *     daemon ──► 纯管道
 *
 * **裁决搬进插件在逻辑上是自洽的**：daemon 只有在转发指令时才需要裁决，而它能转发的
 * 前提就是插件连着。插件不在就没有指令可拦，也就没有「插件掉线了谁来把关」这个洞。
 *
 * fail closed 的三处在这里保持不变：命中拒绝名单 = 拒、确认被拒 = 拒、读配置出错 = 按
 * 最严的默认值办（而不是当成「没配置所以放行」）。
 */
import { CommandError } from "./protocol.ts";

export type ConfirmMode = "silent" | "per_domain" | "always";

/** 由松到紧。顺序有意义：设置页按这个顺序排，比较松紧也靠它。 */
export const CONFIRM_MODES: ConfirmMode[] = ["silent", "per_domain", "always"];

export interface BrowseConfig {
  confirm_mode: ConfirmMode;
  deny_domains: string[];
  approved_domains: string[];
  audit: boolean;
  audit_retention_days: number;
  disabled_features: string[];
  domain_disabled_features: Record<string, string[]>;
}

export const DEFAULTS: BrowseConfig = {
  confirm_mode: "silent",
  deny_domains: [],
  approved_domains: [],
  audit: true,
  audit_retention_days: 7,
  disabled_features: [],
  domain_disabled_features: {},
};

export const CONFIG_KEY = "browse:config";

/**
 * 读配置出错时用的那一份。**不是 DEFAULTS**：读不出来说明存储坏了或权限没了，这时
 * 按最严的模式办比按最松的办安全 —— 宁可多问用户几次，也不要因为读不到配置就把所有
 * 高危动作静默放行。
 */
const ON_ERROR: BrowseConfig = { ...DEFAULTS, confirm_mode: "always" };

// ---------------------------------------------------------------- 读写

export interface Feature {
  /** 配置和设置页共用的 id，也是 i18n key 的后缀。 */
  id: string;
  /** 这个功能覆盖的 wire method，逐字对齐 `handlers/index.ts` 的 HANDLERS。 */
  methods: string[];
}

/**
 * 功能目录：设置页靠它「展示所有支持的功能」，开关也按它分组。`lg:audit.*` 故意不在
 * 里面 —— 那两条是日志唯一的出口，把它们关掉等于把自己锁在自己家外面。
 */
export const FEATURES: Feature[] = [
  {
    id: "tabs",
    methods: [
      "browsingContext.getTree",
      "browsingContext.create",
      "browsingContext.close",
      "browsingContext.activate",
      "browsingContext.navigate",
      "browsingContext.reload",
      "browsingContext.captureScreenshot",
      "lg:tabs.group",
      "lg:tabs.ungroup",
      "lg:tabs.groups",
      "lg:tabs.updateGroup",
    ],
  },
  { id: "script", methods: ["script.evaluate", "script.callFunction"] },
  {
    id: "input",
    methods: ["input.click", "input.type", "input.scroll", "input.key"],
  },
  {
    id: "storage",
    methods: [
      "storage.getCookies",
      "storage.setCookie",
      "storage.deleteCookies",
      "storage.getLocalStorage",
      "storage.setLocalStorage",
    ],
  },
  { id: "network", methods: ["network.subscribe", "network.unsubscribe"] },
  { id: "history", methods: ["lg:history.search", "lg:history.delete"] },
  {
    id: "bookmarks",
    methods: ["lg:bookmarks.search", "lg:bookmarks.create", "lg:bookmarks.remove"],
  },
  { id: "snapshot", methods: ["lg:page.snapshot"] },
  {
    id: "downloads",
    methods: ["lg:downloads.start", "lg:downloads.list", "lg:downloads.cancel", "lg:downloads.open"],
  },
  // 2026-09-16 扩容的能力面（capture/clipboard/readingList/.../printing）。
  {
    id: "capture",
    methods: [
      "lg:pageCapture.saveMhtml",
      "lg:capture.recordTab",
      "lg:capture.recordStop",
      "lg:capture.recordDesktop",
      "lg:offscreen.documents",
    ],
  },
  { id: "clipboard", methods: ["lg:clipboard.read", "lg:clipboard.write"] },
  {
    id: "readingList",
    methods: ["lg:readingList.list", "lg:readingList.add", "lg:readingList.update", "lg:readingList.remove"],
  },
  { id: "topSites", methods: ["lg:topSites.list"] },
  { id: "search", methods: ["lg:search.query"] },
  { id: "wauth", methods: ["lg:wauth.attach", "lg:wauth.detach", "lg:wauth.complete"] },
  {
    id: "ui",
    methods: [
      "lg:commands.list",
      "lg:sidePanel.open",
      "lg:sidePanel.close",
      "lg:sidePanel.behavior",
      "lg:omnibox.setDefault",
    ],
  },
  {
    id: "diagnostics",
    methods: ["lg:processes.list", "lg:system.info", "lg:dns.resolve", "lg:idle.state"],
  },
  { id: "power", methods: ["lg:power.keepAwake", "lg:power.release"] },
  { id: "notifications", methods: ["lg:notifications.show", "lg:notifications.clear"] },
  {
    id: "userScripts",
    methods: [
      "lg:userScripts.register",
      "lg:userScripts.list",
      "lg:userScripts.unregister",
      "lg:userScripts.reset",
      "lg:userScripts.world",
    ],
  },
  { id: "declContent", methods: ["lg:declContent.setRules", "lg:declContent.clear"] },
  { id: "proxy", methods: ["lg:proxy.get", "lg:proxy.set", "lg:proxy.clear"] },
  { id: "gcm", methods: ["lg:gcm.id", "lg:gcm.token", "lg:gcm.deleteToken"] },
  {
    id: "permissions",
    methods: [
      "lg:permissions.getAll",
      "lg:permissions.contains",
    ],
  },
  { id: "printing", methods: ["lg:printing.respond"] },
];

/** method → 所属功能。不在目录里的（`lg:audit.*`）没有开关，永远放行。 */
export const FEATURE_OF: ReadonlyMap<string, Feature> = new Map(
  FEATURES.flatMap((feature) => feature.methods.map((method) => [method, feature])),
);

const KNOWN_FEATURES = new Set(FEATURES.map((feature) => feature.id));

/** feature id 列表 → 只留认识的，去重。存进来的垃圾在这里无声清掉。 */
function normaliseFeatures(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && KNOWN_FEATURES.has(item) && !out.includes(item)) {
      out.push(item);
    }
  }
  return out;
}

/** `{域名: [功能]}` → 域名走同一套规范化（去点去星号小写），空名单的键整个丢掉。 */
function normaliseDomainFeatures(value: unknown): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [raw, ids] of Object.entries((value ?? {}) as Record<string, unknown>)) {
    const features = normaliseFeatures(ids);
    const domain = normaliseDomains([raw])[0];
    if (domain && features.length > 0) {
      out[domain] = features;
    }
  }
  return out;
}

function normalise(raw: unknown): BrowseConfig {
  const got = (raw ?? {}) as Partial<BrowseConfig>;
  const mode = CONFIRM_MODES.includes(got.confirm_mode as ConfirmMode)
    ? (got.confirm_mode as ConfirmMode)
    : DEFAULTS.confirm_mode;
  const days = Number.isInteger(got.audit_retention_days)
    ? (got.audit_retention_days as number)
    : DEFAULTS.audit_retention_days;
  return {
    confirm_mode: mode,
    deny_domains: normaliseDomains(got.deny_domains),
    approved_domains: normaliseDomains(got.approved_domains),
    audit: got.audit !== false,
    audit_retention_days: days,
    disabled_features: normaliseFeatures(got.disabled_features),
    domain_disabled_features: normaliseDomainFeatures(got.domain_disabled_features),
  };
}

export function normaliseDomains(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") {
      continue;
    }
    const domain = item.trim().replace(/^\*/, "").replace(/^\./, "").toLowerCase();
    if (domain && !out.includes(domain)) {
      out.push(domain);
    }
  }
  return out;
}

/**
 * 读配置，读不出来就**抛**。设置页用这个：存储真的坏了，得让用户看见，而不是画一份
 * 看起来正常的默认值让他以为设置还在。
 */
export async function readConfig(): Promise<BrowseConfig> {
  const got = await chrome.storage.local.get(CONFIG_KEY);
  return normalise(got?.[CONFIG_KEY]);
}

/**
 * 读配置，读不出来就退到 `ON_ERROR`（最严的模式）。裁决路径用这个：存储坏了不能让
 * 高危动作因此静默放行。
 */
export async function getConfig(): Promise<BrowseConfig> {
  try {
    return await readConfig();
  } catch {
    return { ...ON_ERROR };
  }
}

/** 合并保存。没提到的字段保持原样，非法值在这里就被挡住。 */
export async function setConfig(patch: Partial<BrowseConfig>): Promise<BrowseConfig> {
  if (patch.confirm_mode !== undefined && !CONFIRM_MODES.includes(patch.confirm_mode)) {
    throw new CommandError("invalid argument", `confirm_mode 非法: ${patch.confirm_mode}`);
  }
  if (patch.audit_retention_days !== undefined && !Number.isInteger(patch.audit_retention_days)) {
    throw new CommandError("invalid argument", "audit_retention_days 要是整数");
  }
  for (const id of patch.disabled_features ?? []) {
    if (!KNOWN_FEATURES.has(id)) {
      throw new CommandError("invalid argument", `不认识的功能: ${id}`);
    }
  }
  for (const ids of Object.values(patch.domain_disabled_features ?? {})) {
    for (const id of ids) {
      if (!KNOWN_FEATURES.has(id)) {
        throw new CommandError("invalid argument", `不认识的功能: ${id}`);
      }
    }
  }
  const merged = normalise({ ...(await getConfig()), ...patch });
  await chrome.storage.local.set({ [CONFIG_KEY]: merged });
  return merged;
}

export async function approve(domain: string): Promise<BrowseConfig> {
  const config = await getConfig();
  if (config.approved_domains.includes(domain)) {
    return config;
  }
  return setConfig({ approved_domains: [...config.approved_domains, domain] });
}

export async function revoke(domain: string): Promise<BrowseConfig> {
  const config = await getConfig();
  return setConfig({
    approved_domains: config.approved_domains.filter((d) => d !== domain),
  });
}

// ---------------------------------------------------------------- 域名

/** URL / 裸域名 → 小写主机名，取不出来返回 null。 */
export function domainOf(url: string | null | undefined): string | null {
  if (!url) {
    return null;
  }
  let host = "";
  try {
    host = new URL(url).hostname;
  } catch {
    host = "";
  }
  if (!host) {
    // cookie 过滤器给的是裸域名（可能带前导点），`URL()` 要么直接抛，要么把
    // `a.test:8080` 解析成「scheme = a.test」并给出空 hostname —— 两种都落这里
    host = (url.split("/")[0] ?? "").split(":")[0] ?? "";
  }
  host = host.trim().replace(/^\./, "").toLowerCase();
  return host || null;
}

/**
 * 域名匹配：写 `example.com` 或 `*.example.com` 都覆盖它本身和全部子域。
 *
 * 拒绝名单上少匹配一条就是漏一个，所以两种写法都取宽的那个语义。
 */
export function domainMatches(domain: string | null, pattern: string): boolean {
  if (!domain) {
    return false;
  }
  const want = pattern.trim().replace(/^\*/, "").replace(/^\./, "").toLowerCase();
  if (!want) {
    return false;
  }
  return domain === want || domain.endsWith(`.${want}`);
}

/**
 * 这条指令冲着哪个页面 / 域名去。cookie 类给的是 `domain`（没有 scheme），页面类给的
 * 是 `url`，浏览器全局的（列书签、搜历史）两个都没有 —— 返回 null。
 */
export function targetUrl(params: Record<string, unknown> | undefined): string | null {
  for (const key of ["url", "domain"]) {
    const value = params?.[key];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return null;
}

/**
 * 拒绝名单这一关。命中就抛，**一条指令都不往下走**。
 *
 * 它管的是**全部**方法，不只高危的那些：拉黑一个域名之后连导航过去都不该允许。
 * `url` 是 dispatch 解析好的**策略目标**（CONTEXT.md）——显式 `url`/`domain`，或
 * 页面方法解析出的真实 tab URL；不是 handler 才知道的那种事后真相。
 */
export async function enforceDenyList(
  method: string,
  url: string | null,
): Promise<void> {
  const { deny_domains: deny } = await getConfig();
  if (deny.length === 0) {
    return;
  }
  const domain = domainOf(url);
  for (const pattern of deny) {
    if (domainMatches(domain, pattern)) {
      throw new CommandError(
        "lg:user rejected",
        `${domain} 命中拒绝名单（deny_domains: ${pattern}），${method} 未执行`,
      );
    }
  }
}

/**
 * 功能开关这一关。全局禁用对一切域名生效；按域名禁用和拒绝名单用同一个策略目标
 * （dispatch 解析好的 url）。没有页面目标的全局动作（列书签、搜历史）只有全局
 * 禁用管得到 —— 它们本来就没有目标域。
 */
export async function enforceFeatureToggles(
  method: string,
  url: string | null,
): Promise<void> {
  const feature = FEATURE_OF.get(method);
  if (!feature) {
    return;
  }
  const { disabled_features, domain_disabled_features } = await getConfig();
  const domain = domainOf(url);
  const off = disabled_features.includes(feature.id)
    || (domain !== null && Object.entries(domain_disabled_features).some(
      ([pattern, ids]) => ids.includes(feature.id) && domainMatches(domain, pattern),
    ));
  if (off) {
    throw new CommandError(
      "lg:feature disabled",
      `功能 ${feature.id} 已被禁用，${method} 未执行`,
    );
  }
}

/**
 * 方法名 → 高危动作名。和 `handlers/confirm.ts` 的 `RiskyAction` 逐字对齐。
 *
 * 这张表只给审计用（记一条指令算不算高危）。**真正的拦截点是各 handler 里那行
 * `confirm()`** —— 它在动手的那一瞬间调用，知道确切的目标 URL，不会像查表那样漏掉
 * 「`input.click` 带了个 `js=` 定位器」这种情况。
 */
export const RISKY_METHODS: Record<string, string> = {
  "storage.getCookies": "readCookies",
  "storage.setCookie": "writeCookies",
  "storage.deleteCookies": "writeCookies",
  "storage.getLocalStorage": "readLocalStorage",
  "storage.setLocalStorage": "writeLocalStorage",
  "script.evaluate": "evalMainWorld",
  "script.callFunction": "evalMainWorld",
  "lg:downloads.start": "download",
  "lg:history.search": "readHistory",
  "lg:history.delete": "writeHistory",
  "lg:bookmarks.search": "readBookmarks",
  "lg:bookmarks.create": "writeBookmarks",
  "lg:bookmarks.remove": "writeBookmarks",
  "lg:pageCapture.saveMhtml": "readPage",
  "lg:capture.recordTab": "captureMedia",
  "lg:capture.recordDesktop": "captureMedia",
  "lg:clipboard.read": "readClipboard",
  "lg:proxy.set": "setProxy",
  "lg:userScripts.register": "registerUserScript",
};

/**
 * 这条指令算高危动作吗，算就返回动作名。
 *
 * `js=` 定位器在 MAIN world 求值（`handlers/input.ts`），spec 4.4 点名它也是高危。
 * 只有 `input.*` 会这么干 —— 别的方法带 `js=` selector 也不会进 MAIN world，
 * 照搬旧口径会把审计动作标错。
 */
export function riskyAction(
  method: string,
  params?: Record<string, unknown>,
): string | null {
  const known = RISKY_METHODS[method];
  if (known) {
    return known;
  }
  if (method.startsWith("input.")) {
    const selector = params?.selector;
    if (typeof selector === "string" && selector.startsWith("js=")) {
      return "evalMainWorld";
    }
  }
  return null;
}

// ---------------------------------------------------------------- 裁决

export type Decision = "allow" | "ask";

/**
 * 这个高危动作要不要问用户。拒绝名单已经在 `enforceDenyList` 里拦过了，这里只判模式。
 *
 * - `silent`：不问
 * - `per_domain`：这个域名同意过就不问，没同意过就问
 * - `always`：每次都问
 */
export async function decide(url: string | null): Promise<Decision> {
  const config = await getConfig();
  if (config.confirm_mode === "silent") {
    return "allow";
  }
  if (config.confirm_mode === "per_domain") {
    const domain = domainOf(url);
    if (domain && config.approved_domains.includes(domain)) {
      return "allow";
    }
  }
  return "ask";
}

/** 用户点了「允许」之后，per_domain 模式要把这个域名记下来。其余模式不记。 */
export async function rememberApproval(url: string | null): Promise<void> {
  const config = await getConfig();
  if (config.confirm_mode !== "per_domain") {
    return;
  }
  const domain = domainOf(url);
  if (domain) {
    await approve(domain);
  }
}
