"""读取 ~/.claude/sessions/ 下 Claude Code 会话信息。

`<pid>.json` 每个文件一条会话，关键字段：name / sessionId / status /
cwd / startedAt / updatedAt（毫秒时间戳）。`<pid>.<hash>.key` 是连接
令牌文件，不是会话，跳过。
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path, PurePath

SESSIONS_DIR = Path.home() / ".claude" / "sessions"

# status 原值 → 中文；没收录的原样输出
STATUS_CN = {"busy": "忙碌", "idle": "空闲"}


def _fmt_ms(ms) -> str:
    """毫秒时间戳 → 本地时间 "MM-DD HH:MM"；缺失给 "-"。"""
    if not ms:
        return "-"
    return time.strftime("%m-%d %H:%M", time.localtime(int(ms) / 1000))


def list_sessions(directory: Path = SESSIONS_DIR) -> list[dict]:
    """扫描目录，返回排好序的会话列表。

    每条字段：pid / name / sessionId / status（已译中文）/ project /
    start / active。
    project = cwd 的 basename；同 basename 但 cwd 不同才算不同项目，
    这时整个 basename 组升级成 "parent/name" 消歧；cwd 相同就是同一个
    项目，保持裸 basename。

    排序：cwd == 当前目录的会话排最前；其余按项目名分组，同项目内按
    开始时间新→旧。坏文件跳过，不让一条坏 JSON 拖死整表。
    """
    sessions = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        sessions.append({
            "pid": data.get("pid") or path.stem,
            "name": data.get("name") or "",
            "sessionId": data.get("sessionId") or "",
            "status": STATUS_CN.get(data.get("status") or "",
                                    data.get("status") or "-"),
            "cwd": str(data.get("cwd") or ""),
            "_start_ms": int(data.get("startedAt") or 0),
            "_active_ms": int(data.get("updatedAt") or 0),
            "start": _fmt_ms(data.get("startedAt")),
            "active": _fmt_ms(data.get("updatedAt")),
        })
    # 消歧：basename 组里出现过 >1 个不同 cwd 才升级成 parent/name
    cwds_by_base: dict[str, set[str]] = defaultdict(set)
    for s in sessions:
        if s["cwd"]:
            cwds_by_base[PurePath(s["cwd"]).name].add(s["cwd"])
    for s in sessions:
        p = PurePath(s["cwd"]) if s["cwd"] else None
        s["project"] = (f"{p.parent.name}/{p.name}"
                        if p and len(cwds_by_base[p.name]) > 1 else
                        (p.name if p else "-"))

    here = os.getcwd()
    sessions.sort(key=lambda s: (
        0 if s["cwd"] == here else 1,   # 当前目录的会话置顶
        s["project"],                    # 同项目聚在一起
        -s["_start_ms"],                 # 项目内开始时间新→旧
    ))
    return sessions


def filter_sessions(sessions: list[dict], session_kw: str | None = None,
                    project: str | None = None,
                    here: str | None = None) -> list[dict]:
    """按参数过滤。-s 关键字对 sessionId 不区分大小写子串匹配；
    -p 精准匹配项目名，值为空串时过滤当前目录所属项目。"""
    if session_kw:
        kw = session_kw.lower()
        sessions = [s for s in sessions if kw in s["sessionId"].lower()]
    if project is not None:
        if project == "":
            here = here or os.getcwd()
            projs = {s["project"] for s in sessions if s["cwd"] == here}
            sessions = [s for s in sessions if s["project"] in projs]
        else:
            sessions = [s for s in sessions if s["project"] == project]
    return sessions


_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Cap per-file read to bound memory + wall-clock on huge conversation files.
# Most metadata (sessionId, cwd, timestamps, title) lives in the first few KB;
# 5 MB covers even very long sessions while skipping the tail of gigabyte-scale logs.
_MAX_JSONL_BYTES = 5 * 1024 * 1024

# --grep 时读得更深：命中串可能藏在长会话中段（实测首偏移 0.7MB~5.1MB），
# 默认 5MB 截断会漏；32MB 覆盖全部会话里除 7 个超大文件外的所有。
_GREP_MAX_JSONL_BYTES = 32 * 1024 * 1024

# 单条字符串超过这个长度不进搜索文本（base64 图片 / 大文件倾倒）
_MAX_HAYSTRING_LEN = 100_000


# 命令包裹 / hook 注入等非人类输入的前缀（真实 jsonl 里 content 为 str 的形态）
_NON_HUMAN_PREFIXES = ("<command-name>", "<command-message>",
                       "<local-command", "<bash-", "<system-reminder>",
                       "<task-notification")


def _user_text_blocks(record: dict) -> str:
    """从 type=user 记录里取真实用户文本；跳过 tool_use/tool_result/image/thinking、
    isMeta/isSidechain（hook 注入、子代理）和命令包裹（<command-…>/<bash-…> 等）。"""
    if record.get("isMeta") or record.get("isSidechain"):
        return ""
    msg = record.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return "" if content.startswith(_NON_HUMAN_PREFIXES) else content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _collect_hay(obj, out: list) -> None:
    """递归收集字符串值进 out；超长字符串（base64 / 文件倾倒）跳过。"""
    if isinstance(obj, str):
        if len(obj) <= _MAX_HAYSTRING_LEN:
            out.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            _collect_hay(x, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_hay(v, out)


def list_session_logs(projects_root: Path = _PROJECTS_ROOT,
                      tail: int = 3,
                      grep: str | None = None) -> list[dict]:
    """Scan projects_root/<hash>/<uuid>.jsonl, return core fields per session.

    Each row: sessionId / start / last / cwd / git_branch / kind /
    summary / first_message / last_messages. Corrupt files are skipped.
    Files larger than ``_MAX_JSONL_BYTES`` are truncated to the first N bytes
    so a multi-hundred-MB conversation log does not dominate wall-clock.

    grep 给定时只保留会话全文（每条记录的所有字符串值，含用户消息、
    助手回复、工具调用、gitBranch、cwd、标题等）包含该子串（不区分
    大小写）的会话，读取上限放宽到 ``_GREP_MAX_JSONL_BYTES``，并在
    人类环境的 stderr 上显示进度条（AI 环境自动禁用）。
    """
    out = []
    if not projects_root.exists():
        return out
    max_bytes = _GREP_MAX_JSONL_BYTES if grep else _MAX_JSONL_BYTES
    needle = grep.lower() if grep else None
    files = sorted(projects_root.glob("*/*.jsonl"))
    prog = task_id = None
    if grep:
        # 函数内 import：进度条只有 grep 慢路径用，别进启动 import 图（test_perf 守）
        from lib.ui import Reporter, progress
        prog = progress(Reporter().console)
        prog.start()
        task_id = prog.add_task("搜索会话", total=len(files))
    for jsonl in files:
        if task_id is not None:
            prog.advance(task_id)
        records = []
        try:
            if jsonl.stat().st_size > max_bytes:
                with jsonl.open("rb") as fh:
                    text = fh.read(max_bytes).decode("utf-8", errors="replace")
            else:
                text = jsonl.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
        if not records:
            continue

        # Single-pass: collect all fields in one sweep over records.
        start_ts = ""
        last_ts = ""
        session_id = ""
        cwd = ""
        git_branch = ""
        kind = ""
        summary = ""
        user_texts: list[str] = []
        hay_parts: list[str] = []

        for r in records:
            if not isinstance(r, dict):
                continue
            if needle:
                _collect_hay(r, hay_parts)
            if not start_ts and r.get("timestamp"):
                start_ts = r["timestamp"]
            last_ts = r.get("timestamp", last_ts)
            if not session_id and r.get("sessionId"):
                session_id = r["sessionId"]
            if not cwd and r.get("cwd"):
                cwd = r["cwd"]
            if not git_branch and r.get("gitBranch"):
                git_branch = r["gitBranch"]
            if not kind and r.get("kind"):
                kind = r["kind"]
            if not summary and r.get("type") == "custom-title" and r.get("customTitle"):
                summary = r["customTitle"]
            if r.get("type") == "user":
                t = _user_text_blocks(r)
                if t:
                    user_texts.append(t)

        if not summary:
            for r in records:
                if isinstance(r, dict) and r.get("type") == "ai-title" and r.get("aiTitle"):
                    summary = r["aiTitle"]
                    break

        if needle and needle not in "\n".join(hay_parts).lower():
            continue

        out.append((last_ts, start_ts, {
            "sessionId": session_id,
            "start": start_ts,
            "last": last_ts,
            "cwd": cwd,
            "git_branch": git_branch,
            "kind": kind,
            "summary": summary,
            "first_message": user_texts[0] if user_texts else "",
            "last_messages": user_texts[-tail:] if user_texts else [],
        }))
    # last desc, start desc, sessionId asc (ISO-8601 strings compare lexicographically)
    if prog is not None:
        prog.stop()
    out.sort(key=lambda t: t[2]["sessionId"])
    out.sort(key=lambda t: t[1], reverse=True)
    out.sort(key=lambda t: t[0], reverse=True)
    return [row for _, _, row in out]


def filter_session_logs(rows: list[dict], grep: str | None = None) -> list[dict]:
    """在 list_session_logs() 输出上加项目消歧并按 grep 过滤。

    项目消歧复用 list_sessions() 规则：basename 组里出现过 >1 个不同 cwd
    才升级成 parent/name；消歧后给每条加 project 字段。
    grep 子串（不区分大小写）命中 summary / first_message / last_messages
    任一元素即保留；grep 为空/None 不过滤。CLI 主路径的全文过滤在
    list_session_logs(grep=)（搜整条会话内容），这里保留作兜底。
    """
    cwds_by_base: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        cwd = r.get("cwd") or ""
        if cwd:
            cwds_by_base[PurePath(cwd).name].add(cwd)
    enriched: list[dict] = []
    for r in rows:
        cwd = r.get("cwd") or ""
        p = PurePath(cwd) if cwd else None
        project = (f"{p.parent.name}/{p.name}"
                   if p and len(cwds_by_base[p.name]) > 1
                   else (p.name if p else "-"))
        nr = dict(r)
        nr["project"] = project
        enriched.append(nr)
    if not grep:
        return enriched
    needle = grep.lower()
    kept = []
    for r in enriched:
        if (needle in (r.get("summary") or "").lower()
                or needle in (r.get("first_message") or "").lower()
                or any(needle in (m or "").lower()
                       for m in r.get("last_messages") or [])):
            kept.append(r)
    return kept
