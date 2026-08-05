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
    "你是 git commit message 生成器。"
    "禁止输出思考过程、分析推理、内心独白或任何解释性文字——直接输出 commit message 本身。"
    "输出必须是且仅是：多行 message（首行 subject + 空行 + body）。"
    "不要解释、不要代码块、不要引号、不要执行任何命令。"
    "subject 格式 type[(scope)]: description（中文，命令式，不超 50 字，不加句号）；"
    "body 用 - 列要点说明变更内容与动机，每行不超 72 字，可多行。"
)


def _lazygophers_enabled() -> bool:
    """LAZYGOPHERS_SCRIPTS_BASE_URL + _TOKEN 均存在时启用 API 路径。"""
    return bool(os.environ.get("LAZYGOPHERS_SCRIPTS_BASE_URL")
                and os.environ.get("LAZYGOPHERS_SCRIPTS_TOKEN"))


def _generate_via_lazygophers(prompt: str, *, system_prompt: str,
                              max_tokens: int = 300, timeout: float = 30.0) -> str:
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
    payload = {
        "model": "haiku",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "disable_thinking": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            _debug_dump(url, payload, raw)
            data = json.loads(raw)
        # Anthropic 风响应：content[].text。haiku 经 proxy 会把思考过程混进
        # text block（disable_thinking 字段对 proxy 无效）——多个 text block 时，
        # 第一块常是散文思考，最后一块才是真正的 message。识别策略：取首个
        # 以 commit type 前缀（feat/fix/...:）开头的块；都没有则回退最后一块。
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return _extract_message(texts).strip()
    except (urllib.error.URLError, urllib.error.HTTPError,
            ValueError, TimeoutError, KeyError, IndexError) as e:
        r.err(f"LAZYGOPHERS API 生成失败: {e}")
        return ""


# commit message subject 前缀 type 列表（与 _build_prompt 规范一致）
_SUBJECT_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf", "test",
    "build", "ci", "chore", "revert", "deps", "config", "security",
)


def _extract_message(texts: list[str]) -> str:
    """从 text block（可能多块、或块内混思考散文）提取真正的 commit message。

    haiku 经 proxy 会无视 disable_thinking，先输出一段思考散文（含 "subject:"/
    "feat:" 等字样但非 message 本体），再输出真正的 message。两种泄漏形态：
    1. 思考与 message 分在不同 text block
    2. 思考与 message 挤在同一 text block（思考在前，message 在后）

    识别：扫所有块所有行，找首行匹配 `<type>[!][(<scope>)]: <desc>` 的位置，
    取该行起到所有块拼接的结尾。都没匹配 → 回退最后一块。
    回退后若结果明显是思考散文（超长或无 commit 结构），返回空串让调用方中止。
    """
    import re
    pat = re.compile(
        r"^(?:(" + "|".join(_SUBJECT_TYPES) + r"))!?(?:\([^)]*\))?:\s+\S"
    )
    blob = "\n".join(t for t in texts if t)
    for i, line in enumerate(blob.split("\n")):
        if pat.match(line.lstrip()):
            return "\n".join(blob.split("\n")[i:]).strip()
    # fallback：最后一块。若超长（>300 字符）说明是思考散文而非 message，拒绝。
    tail = (texts[-1] if texts else "").strip()
    if len(tail) > 300:
        return ""
    return tail


def _debug_dump(url: str, payload: dict, raw: bytes | None = None) -> None:
    """--debug 打印 LAZYGOPHERS 原始请求 body + 响应 body 到 stderr。"""
    from lib.notify import is_debug
    if not is_debug():
        return
    rr = reporter(stderr=True)
    rr.step(f"[debug] LAZYGOPHERS POST {url}")
    rr.step("[debug] request body:")
    rr.output(json.dumps(payload, ensure_ascii=False, indent=2))
    rr.step("[debug] response body:")
    rr.output(raw.decode("utf-8", "replace") if raw else "(无)")


def _has_changes(*, cwd: str | None = None) -> tuple[bool, list[str]]:
    """返回 (有无变更, status --short 行)。"""
    staged = run(["git", "diff", "--cached", "--name-only"], check=False, capture_output=True, cwd=cwd)
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], check=False, capture_output=True, cwd=cwd)
    workdir = run(["git", "diff", "--name-only"], check=False, capture_output=True, cwd=cwd)
    has = bool((staged.stdout or "").strip() or (untracked.stdout or "").strip()
               or (workdir.stdout or "").strip())
    status = run(["git", "status", "--short"], check=False, capture_output=True, cwd=cwd)
    lines = (status.stdout or "").splitlines()
    return has, lines


def run_commit(
    msg: str | None = None,
    *,
    dry_run: bool = False,
    settings_file: str | None = None,
    cwd: str | None = None,
) -> int:
    """自动提交变更。cwd=None 当前目录；批量场景透传各 repo 路径。"""
    r = reporter(stderr=True)
    has, status_lines = _has_changes(cwd=cwd)
    if not has:
        r.ok("没有变更")
        return 0

    r.rule("变更", style="blue")
    for line in status_lines:
        r.info(f"  {line}")

    # 检测暂存区是否已有文件
    staged_p = run(["git", "diff", "--cached", "--name-only"], check=False, capture_output=True, cwd=cwd)
    staged = (staged_p.stdout or "").strip()

    if dry_run:
        branch = current_branch(cwd=cwd)
        r.rule("演练", style="yellow")
        r.kv("dry-run", {"分支": branch, "消息": msg or "（自动生成）"})
        return 0

    # 预清理 stale index.lock（上次 git 进程崩溃残留）。
    # 不等操作失败再清——index.lock 存在时 add/commit 全连锁失败。
    lock_path = ".git/index.lock"
    if cwd:
        lock_path = f"{cwd}/.git/index.lock"
    from os.path import exists
    if exists(lock_path):
        r.step("检测到残留 index.lock，自动清理")
        run(["rm", "-f", lock_path], check=False, cwd=cwd)

    # 暂存区为空 → bit add .（index.lock 冲突自动清理重试）
    if not staged:
        for add_attempt in range(2):
            r.step("bit add .")
            add_p = run(["bit", "add", "."], check=False, capture_output=True, cwd=cwd)
            if add_p.returncode == 0:
                break
            add_err = (add_p.stderr or "") + (add_p.stdout or "")
            if "index.lock" in add_err and add_attempt == 0:
                run(["rm", "-f", ".git/index.lock"], check=False, cwd=cwd)
                continue
            r.err(f"暂存失败：{add_err.strip()[:300]}")
            return 1
        # bit add . 成功 ≠ 暂存区有内容（bit 可能跳过某些文件）。
        # 验证暂存区，空则 fallback git add -A。
        staged_check = run(["git", "diff", "--cached", "--name-only"],
                           check=False, capture_output=True, cwd=cwd)
        if not (staged_check.stdout or "").strip():
            r.step("bit add 未暂存任何文件，回退 git add -A")
            run(["git", "add", "-A"], check=False, cwd=cwd)

    # message 已显式给出 → 直接提交（省 AI 往返）
    # message 缺失 → LAZYGOPHERS env 存在走 /chat/compate API；否则回退 claude CLI
    if msg:
        final_msg = msg
    else:
        prompt = _build_prompt(status_lines, cwd=cwd)
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
                check=False, capture_output=True, cwd=cwd)
        if p.returncode == 0:
            # bit commit 不一定打印 hash，从 git log 取
            hash_p = run(["git", "rev-parse", "--short", "HEAD"], check=False, capture_output=True, cwd=cwd)
            short = (hash_p.stdout or "").strip() or "?"
            branch = current_branch(cwd=cwd) or "detached"
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
            run(["rm", "-f", ".git/index.lock"], check=False, cwd=cwd)
            continue
        r.err(f"提交失败：{err.strip()[:300]}")
        return 1
    return 1


def _build_prompt(status_lines: list[str], *, cwd: str | None = None) -> str:
    # 预注入文件清单 + 真实 diff（截断），AI 不必跑 git 也能写贴合事实的 message。
    # 只给 stat（行数）→ AI 只能编抽象套话，必须喂真实代码变更。
    files_block = "\n".join(f"  {ln}" for ln in status_lines) or "  （无）"
    diff = run(["git", "diff", "--cached"],
               check=False, capture_output=True, cwd=cwd)
    diff_text = (diff.stdout or "").strip()
    # 截断超长 diff（防 token 爆炸 + 慢）：保留前 8000 字符，超出按文件边界裁断
    if len(diff_text) > 8000:
        cut = diff_text.rfind("\n", 0, 8000)
        diff_text = diff_text[: cut if cut > 0 else 8000] + "\n...（diff 已截断）"
    diff_block = diff_text or "（暂存区空）"
    return f"""根据变更生成一条 git commit message。上下文已预收集（勿跑 git，只输出 message）。

<<<DATA>>>
暂存文件（git status --short）：
{files_block}

diff（git diff --cached）：
{diff_block}
<<<END DATA>>>

规范：
- message 多行：首行 subject（type[(scope)]: description，中文，命令式，不超 50 字，不加句号），空行，body（- 列要点说明变更内容与动机，每行不超 72 字，可多行）
- type：feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert / deps / config / security
- 推断：package.json/go.mod→deps, .github/workflows→ci, *_test.*→test, README/注释→docs, 仅格式→style, 其他→feat/fix/chore
- 优先具体 type，避免 chore；breaking → type 后加 !
- subject 必须贴合 diff 实际改动，禁止抽象套话（「重构并优化」「增强稳定性」「提升可维护性」类空泛措辞一律不准）；动词要具体（「拆 X 为 Y」「新增 X 函数」「改 X 条件从 A 到 B」）
- body 只描述 diff 事实（改了什么、为何），禁止臆测动机或价值，禁止「提升可读性/稳定性/性能」等无证据收益

直接输出 message（subject + 空行 + body），无引号无解释。
首行必须以 type: 开头（如 feat:/fix:/refactor: 等），禁止输出思考过程或分析推理。"""


def commit_all(
    root,
    *,
    msg: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> int:
    """批量扫描 root 下所有 git 仓库，逐个自动提交（并行）。

    复用 batch_git.run_batch 框架；每仓 operation 调 run_commit(cwd=repo)。
    无变更的仓库标记 skip。返回 0（全部成功/跳过）或 1（有失败）。
    默认不确认（对齐 push_*）。
    """
    from pathlib import Path
    from lib.batch_git import BatchResult, BatchRunner, CallbackBatchOperation
    from lib.ui import reporter

    r = reporter(stderr=True)
    root = Path(root).resolve()

    def _operation(repo, rr, _root):
        has, _ = _has_changes(cwd=str(repo))
        if not has:
            return "skip", "无变更"
        rc = run_commit(msg, dry_run=dry_run, cwd=str(repo))
        if rc == 0:
            return "ok", "演练" if dry_run else "已提交"
        return "fail", f"退出码 {rc}"

    result: BatchResult = BatchRunner().run(CallbackBatchOperation(
        title="批量 commit",
        root=root,
        folder_name=root.name,
        confirm=confirm,
        detect_fn=_operation,
    ))
    items = [(rr.name, rr.status, rr.detail)
             for rr in (result.succeeded + result.skipped + result.failed)]
    r.status_table(f"批量 commit 结果（{result.total} 仓）", items)
    return 1 if result.failed else 0
