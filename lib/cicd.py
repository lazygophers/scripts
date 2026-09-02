"""CI/CD 状态查询、触发与轮询。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from lib.ai_workflow import ProviderInfo, current_branch, detect_provider, parse_remote_url
from lib.exec import CommandTimeout, run, shell_join
from lib.notify import notify
from lib.ui import reporter

DONE_STATES = {"pass", "fail", "error", "no-checks"}
GH_DONE_CONCLUSIONS = {"success", "neutral", "skipped"}
GH_FAIL_CONCLUSIONS = {"failure", "cancelled", "cancel", "timed_out", "startup_failure", "action_required", "stale"}
GH_PENDING_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}
GLAB_SUCCESS_MARKERS = ("success", "passed", "pass", "skipped")
GLAB_PENDING_MARKERS = ("running", "pending", "created", "waiting", "preparing", "scheduled", "manual")
GLAB_FAILURE_MARKERS = ("failed", "failure", "canceled", "cancelled", "error")


@dataclass
class CiStatus:
    """一次 CI/CD 查询结果。"""

    state: str
    detail: str
    cmd: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class PollConfig:
    """轮询参数。"""

    min_interval: float = 5.0
    max_interval: float = 30.0
    timeout: float | None = None
    once: bool = False
    verbose: bool = False


def resolve_provider(project: str = "") -> ProviderInfo | None:
    """指定项目时按 URL/路径推断 provider；未指定时从当前 git remote 解析。"""
    if not project:
        return detect_provider()
    parsed = parse_remote_url(project)
    host = ""
    repo = project
    if parsed:
        host, repo = parsed
    elif project.startswith("github.com/"):
        host, repo = "github.com", project.split("/", 1)[1]
    provider = "gh" if host == "github.com" or project.startswith("github.com/") else "glab"
    return ProviderInfo(provider=provider, host=host or ("github.com" if provider == "gh" else ""),
                        repo=repo, remote="", remote_url=project)


def _repo_args(info: ProviderInfo) -> list[str]:
    if not info.repo:
        return []
    if info.provider == "gh":
        return ["--repo", info.repo]
    return ["--repo", info.remote_url or info.repo]


def build_status_command(info: ProviderInfo, *, ref: str) -> list[str]:
    """按 provider 生成分支状态查询命令。"""
    if info.provider == "gh":
        return [
            "gh", "run", "list",
            "--branch", ref,
            "--limit", "1",
            "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
            *_repo_args(info),
        ]
    return ["glab", "ci", "status", "--branch", ref, "--output", "json", *_repo_args(info)]


def build_run_status_command(info: ProviderInfo, target: str) -> list[str]:
    """按 provider 生成某次 CI/CD 状态查询命令。"""
    if info.provider == "gh":
        return [
            "gh", "run", "view", target,
            "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
            *_repo_args(info),
        ]
    return ["glab", "ci", "view", target, *_repo_args(info)]


def build_trigger_command(info: ProviderInfo, *, workflow: str, ref: str) -> list[str]:
    """按 provider 生成触发 CI/CD 命令。"""
    if info.provider == "gh":
        return ["gh", "workflow", "run", workflow, "--ref", ref, *_repo_args(info)]
    return ["glab", "ci", "run", "--branch", ref, *_repo_args(info)]


def build_logs_command(info: ProviderInfo, target: str, *, failed: bool = False, job: str = "") -> list[str]:
    """按 provider 生成日志查询命令。"""
    if info.provider == "gh":
        cmd = ["gh", "run", "view", target, "--log-failed" if failed else "--log", *_repo_args(info)]
        if job:
            cmd.extend(["--job", job])
        return cmd
    cmd = ["glab", "ci", "trace", target, *_repo_args(info)]
    if job:
        cmd.extend(["--job", job])
    return cmd


def classify_status(info: ProviderInfo, stdout: str, stderr: str, returncode: int) -> str:
    """把 gh/glab 输出归类成 pass/fail/running/pending/no-checks/error。"""
    if info.provider == "gh":
        return _classify_gh(stdout, stderr, returncode)
    return _classify_glab(stdout, stderr, returncode)


def _classify_gh(stdout: str, stderr: str, returncode: int) -> str:
    text = f"{stdout}\n{stderr}".strip().lower()
    if "no workflow runs" in text or "no runs" in text:
        return "no-checks"
    try:
        rows = json.loads(stdout or "[]")
    except (TypeError, ValueError):
        return "error" if returncode else "pass"
    if not rows:
        return "no-checks"
    row = rows[0] if isinstance(rows, list) else rows
    if not isinstance(row, dict):
        return "error"
    status = str(row.get("status", "")).lower()
    conclusion = str(row.get("conclusion", "")).lower()
    if status in GH_PENDING_STATUSES:
        return "running"
    if conclusion in GH_DONE_CONCLUSIONS:
        return "pass"
    if conclusion in GH_FAIL_CONCLUSIONS or status == "completed":
        return "fail"
    return "error" if returncode else "running"


def _classify_glab(stdout: str, stderr: str, returncode: int) -> str:
    text = f"{stdout}\n{stderr}".strip().lower()
    status = _extract_glab_status(stdout) or _extract_glab_status(stderr)
    if status:
        if any(marker in status for marker in GLAB_FAILURE_MARKERS):
            return "fail"
        if any(marker in status for marker in GLAB_PENDING_MARKERS):
            return "running"
        if any(marker in status for marker in GLAB_SUCCESS_MARKERS):
            return "pass"
    if returncode == 0:
        if any(marker in text for marker in GLAB_FAILURE_MARKERS):
            return "fail"
        if any(marker in text for marker in GLAB_PENDING_MARKERS):
            return "running"
        return "no-checks" if not text else "pass"
    if any(marker in text for marker in GLAB_PENDING_MARKERS):
        return "running"
    if any(marker in text for marker in GLAB_FAILURE_MARKERS):
        return "fail"
    return "error"


def _extract_glab_status(stdout: str) -> str:
    try:
        data = json.loads(stdout or "{}")
    except (TypeError, ValueError):
        return ""
    if isinstance(data, dict):
        for key in ("status", "detailed_status", "state"):
            value = data.get(key)
            if isinstance(value, str):
                return value.lower()
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                return value["text"].lower()
    return ""


def _run_status(cmd: list[str], info: ProviderInfo) -> CiStatus:
    try:
        p = run(cmd, check=False, capture_output=True)
    except CommandTimeout as e:
        return CiStatus("error", str(e), cmd, returncode=124)
    stdout = p.stdout or ""
    stderr = p.stderr or ""
    state = classify_status(info, stdout, stderr, p.returncode)
    detail = (stdout.strip() or stderr.strip() or f"{shell_join(cmd)} exit {p.returncode}")
    return CiStatus(state, detail, cmd, p.returncode, stdout, stderr)


def check_once(info: ProviderInfo, *, ref: str) -> CiStatus:
    """查询一次分支 CI/CD 状态。"""
    return _run_status(build_status_command(info, ref=ref), info)


def check_run_once(info: ProviderInfo, target: str) -> CiStatus:
    """查询一次指定 CI/CD 状态。"""
    return _run_status(build_run_status_command(info, target), info)


def _validate_config(config: PollConfig) -> str | None:
    if config.min_interval < 0 or config.max_interval < 0:
        return "间隔不能小于 0"
    if config.min_interval > config.max_interval:
        return "最小间隔不能大于最大间隔"
    if config.timeout is not None and config.timeout <= 0:
        return "timeout 必须大于 0"
    return None


def _print_final(status: CiStatus, *, attempts: int, elapsed: float) -> None:
    r = reporter(stderr=True)
    style = "green" if status.state == "pass" else "red" if status.state in {"fail", "error"} else "yellow"
    title = "CI/CD 完成" if status.state in DONE_STATES else "CI/CD 未完成"
    r.rule(title, style=style)
    rows = [
        ("状态", status.state, style),
        ("轮询次数", str(attempts), None),
        ("耗时", f"{elapsed:.1f}s", None),
        ("命令", shell_join(status.cmd), None),
    ]
    r.summary("", rows)
    if status.detail.strip():
        r.output(status.detail, max_lines=80, prefix="")


def _resolve_ref(ref: str | None) -> str | None:
    target_ref = ref or current_branch()
    if not target_ref or target_ref == "detached":
        return None
    return target_ref


def _resolve_or_report(project: str = "") -> ProviderInfo | None:
    r = reporter(stderr=True)
    info = resolve_provider(project)
    if info is None:
        r.err("错误: 没有 git remote 或无法解析 provider；请用 --project 指定项目")
        return None
    return info


def status_cicd(ref: str | None = None, *, project: str = "") -> int:
    """查看某个分支的最新 CI/CD。"""
    info = _resolve_or_report(project)
    if info is None:
        return 2
    target_ref = _resolve_ref(ref)
    if target_ref is None:
        reporter(stderr=True).err("错误: 当前不是普通分支，请显式传 ref")
        return 2
    status = check_once(info, ref=target_ref)
    _print_final(status, attempts=1, elapsed=0.0)
    return 0 if status.state == "pass" else 1


def trigger_cicd(workflow: str = "", ref: str | None = None, *, project: str = "") -> int:
    """触发一次 CI/CD。GitHub 需要 workflow 名或 yml 文件；GitLab 触发分支 pipeline。"""
    info = _resolve_or_report(project)
    if info is None:
        return 2
    if info.provider == "glab" and ref is None and workflow:
        ref = workflow
        workflow = ""
    target_ref = _resolve_ref(ref)
    if target_ref is None:
        reporter(stderr=True).err("错误: 当前不是普通分支，请显式传 ref")
        return 2
    if info.provider == "gh" and not workflow:
        reporter(stderr=True).err("错误: GitHub 触发 CI/CD 需要 workflow 名或文件，例如 cicd trigger ci.yml")
        return 2
    cmd = build_trigger_command(info, workflow=workflow, ref=target_ref)
    p = run(cmd, check=False, capture_output=True)
    detail = (p.stdout or "").strip() or (p.stderr or "").strip()
    r = reporter(stderr=True)
    if p.returncode == 0:
        r.ok("CI/CD 已触发")
        if detail:
            r.output(detail, max_lines=80, prefix="")
        return 0
    r.err("CI/CD 触发失败")
    if detail:
        r.output(detail, max_lines=80, prefix="")
    return p.returncode or 1


def logs_cicd(target: str, *, project: str = "", failed: bool = False, job: str = "") -> int:
    """查看某个 CI/CD 的日志。GitHub target 是 run id；GitLab target 通常是 job id。"""
    info = _resolve_or_report(project)
    if info is None:
        return 2
    cmd = build_logs_command(info, target, failed=failed, job=job)
    p = run(cmd, check=False, capture_output=True)
    detail = (p.stdout or "").strip() or (p.stderr or "").strip()
    if detail:
        reporter(stderr=True).output(detail, max_lines=400, prefix="")
    return p.returncode


def watch_cicd(
    ref: str | None = None,
    *,
    target: str = "",
    project: str = "",
    config: PollConfig | None = None,
) -> int:
    """轮询分支或指定 CI/CD，完成后输出最终结果并退出。"""
    config = config or PollConfig()
    err = _validate_config(config)
    r = reporter(stderr=True)
    if err:
        r.err(err)
        return 2

    info = _resolve_or_report(project)
    if info is None:
        return 2

    target_ref = _resolve_ref(ref) if not target else ""
    if not target and target_ref is None:
        r.err("错误: 当前不是普通分支，请显式传 ref")
        return 2

    start = time.monotonic()
    attempts = 0
    last_status: CiStatus | None = None
    try:
        while True:
            attempts += 1
            status = check_run_once(info, target) if target else check_once(info, ref=target_ref or "")
            last_status = status
            if config.verbose:
                name = target or target_ref
                r.info(f"[{attempts}] {name}: {status.state}")
            elif status.state == "error":
                r.err(status.detail)
            if status.state in DONE_STATES or config.once:
                _print_final(status, attempts=attempts, elapsed=time.monotonic() - start)
                notify(f"CI/CD {status.state}")
                return 0 if status.state == "pass" else 1
            if config.timeout is not None and time.monotonic() - start >= config.timeout:
                timeout_status = CiStatus(
                    "error",
                    f"等待超时（{config.timeout:g}s），最后状态: {status.state}",
                    status.cmd,
                    returncode=124,
                    stdout=status.stdout,
                    stderr=status.stderr,
                )
                _print_final(timeout_status, attempts=attempts, elapsed=time.monotonic() - start)
                return 1
            delay = random.uniform(config.min_interval, config.max_interval)
            time.sleep(delay)
    except KeyboardInterrupt:
        if last_status is None:
            r.warn("用户中断，尚未完成第一次查询")
            return 130
        _print_final(last_status, attempts=attempts, elapsed=time.monotonic() - start)
        return 130
