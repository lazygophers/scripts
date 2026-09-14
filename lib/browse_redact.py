"""脱敏：写盘前把密钥、令牌、凭据换成 `[REDACTED]`。

**顺序不可颠倒：先脱敏再截断。** 截断先跑的话 `sk-ant-api03-xxxx…` 只剩个头，
正则匹配不到，半截 token 照样落在审计文件里。所以对外只给 `clean()` 一个入口 ——
它把两步按正确顺序绑死，调用方没有写反的机会（`redact` / `truncate` 也导出，
只为单测能分别验，业务代码用 `clean`）。

9 条 secret 正则照 `AgentDeskAI/browser-tools-mcp:chrome-extension/shared.js:180-245`
（MIT），外加 JWT 三段判定、HTTP 认证方案（`Bearer/Basic/Token/Digest`）、
以及 key 名启发式（字段名看着像密码就整值抹掉，不看内容）。

key 名启发式宁可多抹：`auth` 会连 `author` 一起命中。审计日志里多抹一个字段
比漏一个凭据划算。
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# 审计一行的默认长度上限。够放下方法名 + URL + 错误消息，放不下页面正文。
DEFAULT_LIMIT = 2048

# 9 条 secret 正则。名字就是测试里逐条点名的那个 key。
PATTERNS: dict[str, re.Pattern[str]] = {
    # PEM 私钥放第一条：它跨多行，先整块吃掉，免得里面的 base64 被别的规则切碎
    "pem_private_key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S,
    ),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "stripe_key": re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    # JWT：header.payload.signature 三段 base64url，且 header 以 `eyJ`（`{"` 的
    # base64）开头 —— 只靠「三段点分」会把版本号之类的误伤
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
}

# HTTP 认证方案：方案名保留，凭据部分抹掉，这样日志里还看得出「用了 Bearer」
_AUTH_SCHEME = re.compile(r"\b(Bearer|Basic|Token|Digest)\s+[A-Za-z0-9\-._~+/=]{8,}")

# 字段名启发式：命中就把整个值抹掉，不管值长什么样
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|credential|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|auth|cookie|session|signature|jwt",
    re.I,
)


def redact(text: str) -> str:
    """把文本里的密钥、令牌、认证头换成 `[REDACTED]`。"""
    for pattern in PATTERNS.values():
        text = pattern.sub(REDACTED, text)
    return _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


def truncate(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """超长就截断，并标出砍掉多少字符。"""
    if limit < 0 or len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit})"


def is_sensitive_key(key: str) -> bool:
    """字段名看着像凭据吗。"""
    return bool(_SENSITIVE_KEY.search(key))


def clean(value, limit: int = DEFAULT_LIMIT):
    """递归脱敏 + 截断，**顺序固定**。dict / list / 标量都吃。

    字段名命中启发式的，整个值直接换成 `[REDACTED]`，不再看内容。
    """
    if isinstance(value, dict):
        return {
            str(k): REDACTED if is_sensitive_key(str(k)) else clean(v, limit)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [clean(v, limit) for v in value]
    if isinstance(value, str):
        return truncate(redact(value), limit)  # 先脱敏，再截断
    return value
