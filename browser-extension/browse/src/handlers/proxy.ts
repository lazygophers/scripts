import { CommandError, optionalString, requireString } from "../protocol.ts";
import { confirm } from "./confirm.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.proxy.settings`：改的是**整个浏览器**的代理，所以 set 过确认（高危、
 * 无目标域名）。
 */

const MODES = ["direct", "system", "fixed_servers", "pac_script"] as const;
type Mode = (typeof MODES)[number];

/**
 * `ProxyServer.scheme` 的取值（chrome.proxy 文档的 Scheme 类型）。不给就是 http
 * ——那是 Chrome 自己的默认值，不是这里的选择。
 */
const SCHEMES = ["http", "https", "quic", "socks4", "socks5"] as const;
type Scheme = (typeof SCHEMES)[number];

function proxyConfig(params: Record<string, unknown>): chrome.proxy.ProxyConfig {
  const mode = requireString(params.mode, "mode") as Mode;
  if (!(MODES as readonly string[]).includes(mode)) {
    throw new CommandError("invalid argument", `mode must be one of ${MODES.join(", ")}`);
  }
  if (mode === "fixed_servers") {
    const host = requireString(params.host, "host", " (fixed_servers needs it)");
    const port = params.port;
    if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) {
      throw new CommandError("invalid argument", "port must be an integer in [1, 65535]");
    }
    const scheme = optionalString(params.scheme, "scheme") ?? "http";
    if (!(SCHEMES as readonly string[]).includes(scheme)) {
      throw new CommandError("invalid argument", `scheme must be one of ${SCHEMES.join(", ")}`);
    }
    return {
      mode,
      rules: { singleProxy: { scheme: scheme as Scheme, host, port: port as number } },
    };
  }
  if (mode === "pac_script") {
    const pacUrl = optionalString(params.pacUrl, "pacUrl");
    const pacData = optionalString(params.pacData, "pacData");
    if (pacUrl === undefined && pacData === undefined) {
      throw new CommandError("invalid argument", "pac_script needs pacUrl or pacData");
    }
    return {
      mode,
      pacScript: pacUrl !== undefined ? { url: pacUrl } : { data: pacData as string },
    };
  }
  return { mode };
}

export async function proxySet(params: Record<string, unknown>): Promise<{ mode: string }> {
  requireApi("proxy", "changing proxy settings");
  const value = proxyConfig(params);
  await confirm({ action: "setProxy", method: "lg:proxy.set", url: null });
  await chrome.proxy.settings.set({ value, scope: "regular" });
  return { mode: value.mode };
}

export async function proxyGet(): Promise<unknown> {
  requireApi("proxy", "reading proxy settings");
  // 旧版 @types 把 settings.get 标成 void；运行时回 {value, levelOfControl}
  const get = chrome.proxy.settings.get as () => Promise<{
    value: { mode: string };
    levelOfControl: string;
  }>;
  const details = await get();
  return { mode: details.value.mode, levelOfControl: details.levelOfControl };
}

/** 恢复成「不由扩展控制」（交回浏览器自己的设置）。 */
export async function proxyClear(): Promise<{ cleared: true }> {
  requireApi("proxy", "clearing proxy settings");
  await chrome.proxy.settings.clear({ scope: "regular" });
  return { cleared: true };
}
