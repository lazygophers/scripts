"""CI/CD 状态轮询。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from lib.ai_workflow import ProviderInfo, current_branch, detect_provider
from lib.exec import CommandTimeout, run, shell_join
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


def build_status_command(info: ProviderInfo, *, ref: str) -> list[str]:
    """按 provider 生成状态查询命令。"""
    if info.provider == "gh":
        return [
            "gh", "run", "list",
            "--branch", ref,
            "--limit", "1",
            "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
        ]
    return ["glab", "ci", "status", "--branch", ref, "--output", "json"]


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
    status = _extract_glab_status(stdout)
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


def check_once(info: ProviderInfo, *, ref: str) -> CiStatus:
    """查询一次 CI/CD 状态。"""
    cmd = build_status_command(info, ref=ref)
    try:
        p = run(cmd, check=False, capture_output=True)
    except CommandTimeout as e:
        return CiStatus("error", str(e), cmd, returncode=124)
    stdout = p.stdout or ""
    stderr = p.stderr or ""
    state = classify_status(info, stdout, stderr, p.returncode)
    detail = (stdout.strip() or stderr.strip() or f"{shell_join(cmd)} exit {p.returncode}")
    return CiStatus(state, detail, cmd, p.returncode, stdout, stderr)


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


def watch_cicd(ref: str | None = None, *, config: PollConfig | None = None) -> int:
    """轮询当前分支/指定 ref 的 CI/CD，完成后输出最终结果并退出。"""
    config = config or PollConfig()
    err = _validate_config(config)
    r = reporter(stderr=True)
    if err:
        r.err(err)
        return 2

    info = detect_provider()
    if info is None:
        r.err("错误: 没有 git remote 或无法解析 provider")
        return 2

    target_ref = ref or current_branch()
    if not target_ref or target_ref == "detached":
        r.err("错误: 当前不是普通分支，请显式传 ref")
        return 2

    start = time.monotonic()
    attempts = 0
    last_status: CiStatus | None = None
    try:
        while True:
            attempts += 1
            status = check_once(info, ref=target_ref)
            last_status = status
            if config.verbose:
                r.info(f"[{attempts}] {target_ref}: {status.state}")
            if status.state in DONE_STATES or config.once:
                _print_final(status, attempts=attempts, elapsed=time.monotonic() - start)
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
