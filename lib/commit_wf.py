"""commit 工作流：检测变更 → 拼 prompt → 调 AI 生成 message → bit commit。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from lib.ai_workflow import current_branch, generate_via_claude
from lib.exec import run
from lib.ui import reporter


_COMMIT_SYSTEM = (
    "你是 git commit message 生成器。输出多行 message（首行 subject + 空行 + body），"
    "不要解释、不要代码块、不要引号、不要执行任何命令。"
    "subject 格式 type[(scope)]: description（中文，命令式，不超 50 字，不加句号）；"
    "body 用 - 列要点说明变更内容与动机，每行不超 72 字，可多行。"
)


def _lazygophers_enabled() -> bool:
    """LAZYGOPHERS_SCRIPTS_BASE_URL + _TOKEN 均存在时启用 API 路径。"""
    return bool(os.environ.get("LAZYGOPHERS_SCRIPTS_BASE_URL")
                and os.environ.get("LAZYGOPHERS_SCRIPTS_TOKEN"))


def _generate_via_lazygophers(prompt: str, *, system_prompt: str,
                              max_tokens: int = 200, timeout: float = 30.0) -> str:
    """调 LAZYGOPHERS /chat/compate（Anthropic 风格式）生成 message。

    请求：POST {BASE_URL}/chat/compate，body {model, max_tokens, system, messages}，
    鉴权 Authorization: Bearer <token>，响应 content[].text（Anthropic 风）。
    默认禁 thinking（commit 生成无需 extended thinking）。
    Returns: 生成文本（strip）。失败返回空串。
    """
    r = reporter(stderr=True)
    base = os.environ["LAZYGOPHERS_SCRIPTS_BASE_URL"].rstrip("/")
    token = os.environ["LAZYGOPHERS_SCRIPTS_TOKEN"]
    url = f"{base}/chat/compate"
    body = json.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "disable_thinking": True,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        # Anthropic 风响应：content[].text（实测 /chat/compate）
        parts = data.get("content") or []
        return "".join(p.get("text", "") for p in parts
                       if p.get("type") == "text").strip()
    except (urllib.error.URLError, urllib.error.HTTPError,
            ValueError, TimeoutError, KeyError, IndexError) as e:
        r.err(f"LAZYGOPHERS API 生成失败: {e}")
        return ""


def _has_changes() -> tuple[bool, list[str]]:
    """返回 (有无变更, status --short 行)。"""
    staged = run(["git", "diff", "--cached", "--name-only"], check=False, capture_output=True)
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], check=False, capture_output=True)
    workdir = run(["git", "diff", "--name-only"], check=False, capture_output=True)
    has = bool((staged.stdout or "").strip() or (untracked.stdout or "").strip()
               or (workdir.stdout or "").strip())
    status = run(["git", "status", "--short"], check=False, capture_output=True)
    lines = (status.stdout or "").splitlines()
    return has, lines


def run_commit(
    msg: str | None = None,
    *,
    dry_run: bool = False,
    settings_file: str | None = None,
) -> int:
    """自动提交变更。"""
    r = reporter(stderr=True)
    has, status_lines = _has_changes()
    if not has:
        r.ok("没有变更")
        return 0

    r.rule("变更", style="blue")
    for line in status_lines:
        r.info(f"  {line}")

    # 检测暂存区是否已有文件
    staged_p = run(["git", "diff", "--cached", "--name-only"], check=False, capture_output=True)
    staged = (staged_p.stdout or "").strip()

    if dry_run:
        branch = current_branch()
        r.rule("演练", style="yellow")
        r.kv("dry-run", {"分支": branch, "消息": msg or "（自动生成）"})
        return 0

    # 暂存区为空 → bit add .
    if not staged:
        r.step("bit add .")
        run(["bit", "add", "."], check=False)

    # message 已显式给出 → 直接提交（省 AI 往返）
    # message 缺失 → LAZYGOPHERS env 存在走 /chat/compate API；否则回退 claude CLI
    if msg:
        final_msg = msg
    else:
        prompt = _build_prompt(status_lines)
        if _lazygophers_enabled():
            r.step("LAZYGOPHERS /chat/compate 生成 message（禁 thinking）")
            final_msg = _generate_via_lazygophers(prompt, system_prompt=_COMMIT_SYSTEM)
        else:
            final_msg = generate_via_claude(prompt, system_prompt=_COMMIT_SYSTEM)
        if not final_msg:
            r.err("生成 message 失败，已取消提交")
            return 1
        r.step(f"生成 message: {final_msg}")

    # index.lock 冲突先清理再重试
    for attempt in range(2):
        p = run(["bit", "commit", "--no-verify", "-m", final_msg],
                check=False, capture_output=True)
        if p.returncode == 0:
            # bit commit 不一定打印 hash，从 git log 取
            hash_p = run(["git", "rev-parse", "--short", "HEAD"], check=False, capture_output=True)
            short = (hash_p.stdout or "").strip() or "?"
            branch = current_branch() or "detached"
            r.panel(
                f"提交完成  {short}",
                f"hash   {short}\n"
                f"branch {branch}\n"
                f"\n—— message ——\n{final_msg}",
                style="green",
            )
            return 0
        err = (p.stderr or "") + (p.stdout or "")
        if "index.lock" in err and attempt == 0:
            run(["rm", "-f", ".git/index.lock"], check=False)
            continue
        r.err(f"提交失败：{err.strip()[:300]}")
        return 1
    return 1


def _build_prompt(status_lines: list[str]) -> str:
    # 预注入文件清单 + diff stat，claude 不必自己跑 git
    files_block = "\n".join(f"  {ln}" for ln in status_lines) or "  （无）"
    stat = run(["git", "diff", "--cached", "--stat"], check=False, capture_output=True)
    stat_block = (stat.stdout or "").strip() or "（暂存区空）"
    return f"""根据变更生成一条 git commit message。上下文已预收集（勿跑 git，只输出 message）。

<<<DATA>>>
暂存文件（git status --short）：
{files_block}

diff --stat：
{stat_block}
<<<END DATA>>>

规范：
- message 多行：首行 subject（type[(scope)]: description，中文，命令式，不超 50 字，不加句号），空行，body（- 列要点说明变更内容与动机，每行不超 72 字，可多行）
- type：feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert / deps / config / security
- 推断：package.json/go.mod→deps, .github/workflows→ci, *_test.*→test, README/注释→docs, 仅格式→style, 其他→feat/fix/chore
- 优先具体 type，避免 chore；breaking → type 后加 !

直接输出 message（subject + 空行 + body），无引号无解释。"""
