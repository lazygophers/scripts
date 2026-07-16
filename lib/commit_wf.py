"""commit 工作流：检测变更 → 拼 prompt → 调 claude 生成 message → bit commit。"""

from __future__ import annotations

from lib.ai_workflow import current_branch, generate_text
from lib.exec import run
from lib.ui import reporter


_COMMIT_SYSTEM = (
    "你是 git commit message 生成器。只输出一行 message 文本，不要解释、"
 "不要代码块、不要引号、不要执行任何命令。格式 type[(scope)]: description"
 "（中文，命令式，不超 50 字，不加句号）。"
)


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

    # message 已显式给出 → 直接提交（省 claude 往返）
    # message 缺失 → claude --bare 纯生成 message（不执行工具），Python 侧提交
    if msg:
        final_msg = msg
    else:
        prompt = _build_prompt(status_lines)
        final_msg = generate_text(
            prompt,
            system_prompt=_COMMIT_SYSTEM,
            max_tokens=80,
        )
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
            r.ok(f"提交完成：{short}")
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
- message：type[(scope)]: description（中文，命令式，不超 50 字，不加句号）
- type：feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert / deps / config / security
- 推断：package.json/go.mod→deps, .github/workflows→ci, *_test.*→test, README/注释→docs, 仅格式→style, 其他→feat/fix/chore
- 优先具体 type，避免 chore；breaking → type 后加 !

只输出一行 message，无引号无解释。"""
