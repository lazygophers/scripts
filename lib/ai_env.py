"""检测当前 shell 环境是否由 AI 工具派生（Claude Code / Codex / Cursor 等）。

依据 .scratch/research/detect-ai-shell-env.md：
- CLAUDECODE=1：Claude Code 官方文档明文支持此检测（Bash/PowerShell 工具、
  tmux、hook、statusline、stdio MCP 子进程都会设置）
- CODEX_SANDBOX：openai/codex 源码注入（macOS 值为 "seatbelt"）；
  CODEX_SANDBOX_NETWORK_DISABLED 同样只有 Codex 会设置
- CURSOR_AGENT / GEMINI_CLI：推测（未取得一手来源），命中即信

只查环境变量，不做祖先进程链兜底：省一次 ps 调用，且 CI/cron 等无 TTY
场景本来也只能证明「非交互」而非「是 AI」。
# ponytail: 环境变量可伪造可 unset，仅适合体验分流（换输出、跳确认），
# 不能当安全边界；新工具出现时往 _MARKERS 加一行即可
"""
from __future__ import annotations

import json
import os

# 变量名 → 工具名。判断只看「存在且非空」，CLAUDECODE 额外要求 == "1"。
_MARKERS = {
    "CLAUDECODE": "claude-code",
    "CLAUDE_CODE_ENTRYPOINT": "claude-code",
    "CODEX_SANDBOX": "codex",
    "CODEX_SANDBOX_NETWORK_DISABLED": "codex",
    "CURSOR_AGENT": "cursor",
    "GEMINI_CLI": "gemini-cli",
}


def ai_tool_name(environ: dict[str, str] | None = None) -> str | None:
    """命中标记变量时返回工具名（如 "claude-code"），否则 None。"""
    env = os.environ if environ is None else environ
    for var, tool in _MARKERS.items():
        value = env.get(var)
        if not value:
            continue
        if var == "CLAUDECODE" and value != "1":
            continue
        return tool
    return None


def is_ai_shell_env(environ: dict[str, str] | None = None) -> bool:
    """当前进程是否运行在 AI 工具派生的 shell 环境里。"""
    return ai_tool_name(environ) is not None


def json_dumps(value: object) -> str:
    """序列化 CLI 数据；AI 环境去缩进和空格，保留全部字段和值。"""
    if is_ai_shell_env():
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=2)
