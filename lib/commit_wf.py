"""commit 工作流：检测变更 → 拼 prompt → 调 claude bit commit。"""

from __future__ import annotations

from lib.ai_workflow import current_branch, run_claude
from lib.exec import run
from lib.ui import reporter


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

    prompt = _build_prompt(msg, status_lines)
    return run_claude(
        prompt,
        system_prompt="你是 git commit 助手，根据用户输入直接执行 bit commit 命令，不要解释。",
        settings_file=settings_file,
    )


def _build_prompt(msg: str | None, status_lines: list[str]) -> str:
    # 预注入文件清单 + diff stat，claude 不必自己跑 git
    files_block = "\n".join(f"  {ln}" for ln in status_lines) or "  （无）"
    stat = run(["git", "diff", "--cached", "--stat"], check=False, capture_output=True)
    stat_block = (stat.stdout or "").strip() or "（暂存区空）"
    return f"""提交当前变更。上下文已预收集（勿重复跑 git）。

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

用户输入 '{msg or '无'}'：符合规范直接用，否则据变更生成。

直接执行 bit commit --no-verify -m \"<message>\"，不要只输出文本。index.lock 冲突先 rm -f .git/index.lock 再重试。"""
