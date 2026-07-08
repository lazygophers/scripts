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

    prompt = _build_prompt(msg)
    return run_claude(
        prompt,
        system_prompt="你是 git commit 助手，根据用户输入直接执行 bit commit 命令，不要解释。",
        settings_file=settings_file,
    )


def _build_prompt(msg: str | None) -> str:
    return f"""提交当前变更。

步骤：
1. git diff --cached --name-only 确认暂存文件
2. git ls-files --others --exclude-standard 确认新文件
3. 新文件若为本地工具/缓存/编译产物，用 bit reset HEAD <file> 取消暂存
4. 直接执行 bit commit --no-verify -m \"<message>\"（跳过 hooks）

规范：
- message：type[(scope)]: description（中文，命令式，不超 50 字，不加句号）
- type：feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert / deps / config / security
- 推断规则：
  · package.json / go.mod 变更 → deps
  · .github/workflows 变更 → ci
  · *_test.go / *.spec.ts 变更 → test
  · README / 注释 变更 → docs
  · 仅缩进/分号 变更 → style
  · 其他 → feat / fix / chore
- 用户输入 '{msg or '无'}'：若符合规范直接用，否则根据变更生成
- 优先用具体 type，避免 chore
- breaking change：type 后加 !

直接执行 bit commit，不要只输出文本。如遇 index.lock 先 rm -f .git/index.lock 再重试。"""
