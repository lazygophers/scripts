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
