/**
 * 脱敏：进审计之前把密钥、令牌、凭据换成 `[REDACTED]`。
 *
 * **顺序不可颠倒：先脱敏再截断。** 截断先跑的话 `sk-ant-api03-xxxx…` 只剩个头，正则
 * 匹配不到，半截 token 照样落进审计。所以对外只给 `clean()` 一个入口 —— 它把两步按
 * 正确顺序绑死，调用方没有写反的机会（`redact` / `truncate` 也导出，只为单测能分别
 * 验，业务代码一律用 `clean`）。
 *
 * 9 条 secret 正则照 `AgentDeskAI/browser-tools-mcp:chrome-extension/shared.js:180-245`
 * （MIT），外加 JWT 三段判定、HTTP 认证方案（`Bearer/Basic/Token/Digest`）、以及 key
 * 名启发式（字段名看着像密码就整值抹掉，不看内容）。
 *
 * key 名启发式宁可多抹：`auth` 会连 `author` 一起命中。审计里多抹一个字段比漏一个
 * 凭据划算。
 *
 * 这个模块原来在 Python 侧（`lib/browse_redact.py`）。2026-09-14 审计搬进插件之后跟着
 * 搬过来 —— 脱敏必须和写审计在同一个进程里，隔着一条管道就没法保证顺序。
 */

export const REDACTED = "[REDACTED]";

/** 审计一行的默认长度上限。够放下方法名 + URL + 错误消息，放不下页面正文。 */
export const DEFAULT_LIMIT = 2048;

/** 9 条 secret 正则。名字就是测试里逐条点名的那个 key。 */
export const PATTERNS: Record<string, RegExp> = {
  // PEM 私钥放第一条：它跨多行，先整块吃掉，免得里面的 base64 被别的规则切碎
  pem_private_key: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  aws_access_key: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
  github_pat: /\bgithub_pat_[A-Za-z0-9_]{22,}/g,
  github_token: /\bgh[pousr]_[A-Za-z0-9]{36,}/g,
  anthropic_key: /\bsk-ant-[A-Za-z0-9_-]{20,}/g,
  stripe_key: /\bsk_live_[A-Za-z0-9]{16,}/g,
  slack_token: /\bxox[baprs]-[A-Za-z0-9-]{10,}/g,
  google_api_key: /\bAIza[0-9A-Za-z_-]{35}\b/g,
  // JWT：header.payload.signature 三段 base64url，且 header 以 `eyJ`（`{"` 的 base64）
  // 开头 —— 只靠「三段点分」会把版本号之类的误伤
  jwt: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+/g,
};

// HTTP 认证方案：方案名保留，凭据部分抹掉，这样日志里还看得出「用了 Bearer」
const AUTH_SCHEME = /\b(Bearer|Basic|Token|Digest)\s+[A-Za-z0-9\-._~+/=]{8,}/g;

// 字段名启发式：命中就把整个值抹掉，不管值长什么样
const SENSITIVE_KEY =
  /pass(word|wd)?|secret|token|credential|api[_-]?key|access[_-]?key|private[_-]?key|auth|cookie|session|signature|jwt/i;

/** 把文本里的密钥、令牌、认证头换成 `[REDACTED]`。 */
export function redact(text: string): string {
  let out = text;
  for (const pattern of Object.values(PATTERNS)) {
    out = out.replace(pattern, REDACTED);
  }
  return out.replace(AUTH_SCHEME, (_match, scheme: string) => `${scheme} ${REDACTED}`);
}

/** 超长就截断，并标出砍掉多少字符。 */
export function truncate(text: string, limit: number = DEFAULT_LIMIT): string {
  if (limit < 0 || text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit)}…(+${text.length - limit})`;
}

/** 字段名看着像凭据吗。 */
export function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEY.test(key);
}

/**
 * 递归脱敏 + 截断，**顺序固定**。对象 / 数组 / 标量都吃。
 *
 * 字段名命中启发式的，整个值直接换成 `[REDACTED]`，不再看内容。
 */
export function clean(value: unknown, limit: number = DEFAULT_LIMIT): unknown {
  if (typeof value === "string") {
    return truncate(redact(value), limit); // 先脱敏，再截断
  }
  if (Array.isArray(value)) {
    return value.map((item) => clean(item, limit));
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = isSensitiveKey(key) ? REDACTED : clean(item, limit);
    }
    return out;
  }
  return value;
}
