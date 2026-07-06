"""AI workflow 共享工具：git remote/provider 检测 + claude CLI 调用。

commit / prc / issue 三个脚本的公共逻辑。
"""
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from lib.exec import run, run_no_capture
from lib.ui import reporter


def fmt_opt(flag: str, value: str | None) -> str:
    """格式化 CLI flag, value 为 None/空时返回空串。value 用 shlex.quote 防注入。"""
    if not value:
        return ""
    return f"{flag} {shlex.quote(value)}"


# ssh.gitlab.starpago.com → gitlab.starpago.com
_HOST_NORMALIZE = {
    "ssh.gitlab.starpago.com": "gitlab.starpago.com",
    "ssh.github.com": "github.com",
}

_REMOTE_URL_RE = re.compile(
    r"^(?!https?://)(?:ssh://)?(?:[^/@]+@)?(?P<host>[^:/]+)(?::[0-9]+)?[:/](?P<path>.+?)(?:\.git)?$|"
    r"^https?://(?:[^/@]+@)?(?P<host2>[^/:]+)(?::[0-9]+)?/(?P<path2>.+?)(?:\.git)?$",
)


@dataclass
class ProviderInfo:
    """检测到的 provider 与仓库元信息。"""
    provider: str  # "gh" | "glab"
    host: str      # normalized host, e.g. "github.com" / "gitlab.starpago.com"
    repo: str      # owner/repo 路径
    remote: str    # remote 名
    remote_url: str


def _normalize_host(raw: str) -> str:
    return _HOST_NORMALIZE.get(raw, raw)


def parse_remote_url(url: str) -> tuple[str, str] | None:
    """从 remote URL 解析 (host, repo_path)。无法解析返回 None。"""
    m = _REMOTE_URL_RE.match(url)
    if not m:
        return None
    host = m.group("host") or m.group("host2") or ""
    path = m.group("path") or m.group("path2") or ""
    if not host or not path:
        return None
    return _normalize_host(host), path


def git_toplevel() -> Path | None:
    """当前 git 仓库根目录，不在仓库内返回 None。"""
    p = run(["git", "rev-parse", "--show-toplevel"], check=False, capture_output=True)
    if p.returncode != 0:
        return None
    out = (p.stdout or "").strip()
    return Path(out) if out else None


def current_branch(*, cwd: str | None = None) -> str:
    """当前分支名（detached HEAD 返回 'detached'）。"""
    p = run(["git", "symbolic-ref", "--short", "HEAD"], check=False, capture_output=True, cwd=cwd)
    if p.returncode == 0:
        return (p.stdout or "").strip()
    return "detached"


def primary_remote(*, cwd: str | None = None) -> str | None:
    """首选 remote 名（当前分支配置的 upstream，否则第一个 remote）。"""
    branch = current_branch(cwd=cwd)
    if branch and branch != "detached":
        p = run(["git", "config", "--get", f"branch.{branch}.remote"],
                check=False, capture_output=True, cwd=cwd)
        if p.returncode == 0 and (p.stdout or "").strip():
            return (p.stdout or "").strip()
    p = run(["git", "remote"], check=False, capture_output=True, cwd=cwd)
    remotes = (p.stdout or "").split()
    return remotes[0] if remotes else None


def detect_provider(*, cwd: str | None = None) -> ProviderInfo | None:
    """检测当前仓库的 git provider。无 remote 返回 None。"""
    remote = primary_remote(cwd=cwd)
    if not remote:
        return None
    url_p = run(["git", "remote", "get-url", remote], check=False, capture_output=True, cwd=cwd)
    remote_url = (url_p.stdout or "").strip()
    if not remote_url:
        return None
    parsed = parse_remote_url(remote_url)
    if not parsed:
        return None
    host, repo = parsed
    provider = "gh" if host == "github.com" else "glab"
    return ProviderInfo(
        provider=provider, host=host, repo=repo, remote=remote, remote_url=remote_url
    )


def detect_self_assignee(info: ProviderInfo) -> str:
    """获取当前用户名（gh / glab 各自的 API）。失败返回空串。"""
    if info.provider == "gh":
        p = run(["gh", "api", "user", "--jq", ".login"], check=False, capture_output=True)
        if p.returncode == 0:
            return (p.stdout or "").strip()
    else:  # glab
        p = run(["glab", "api", "--hostname", info.host, "user"],
                check=False, capture_output=True)
        if p.returncode == 0:
            import json
            try:
                d = json.loads(p.stdout or "")
                return d.get("username", "")
            except (ValueError, TypeError):
                pass
    return ""


def remote_default_branch(remote: str, *, cwd: str | None = None) -> str:
    """远端默认分支（refs/remotes/<remote>/HEAD），失败回退 'main'。"""
    p = run(["git", "symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"],
            check=False, capture_output=True, cwd=cwd)
    if p.returncode == 0:
        out = (p.stdout or "").strip()
        # 形如 origin/main → 去掉 remote 前缀
        if "/" in out:
            return out.split("/", 1)[1]
        return out
    return "main"


_CLAUDE_BASE_ARGS = [
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--setting-sources", "",
    "--permission-mode=bypassPermissions",
    "--dangerously-skip-permissions",
    "--tools", "Bash",
]


def run_claude(
    prompt: str,
    *,
    system_prompt: str,
    settings_file: str | None = None,
) -> int:
    """调用 claude -p 执行 prompt, 透传 stdout/stderr。

    Returns:
        claude 进程退出码
    """
    r = reporter(stderr=True)
    args = ["claude", "-p", *_CLAUDE_BASE_ARGS,
            "--system-prompt", system_prompt]
    if settings_file:
        args += ["--settings", settings_file]
    args += [prompt]
    # 透传 stdio: 不 capture, 直接打到当前终端; run_no_capture 隔离进程组保证 Ctrl-C 只送 claude
    rc = run_no_capture(args)
    if rc != 0:
        r.err(f"claude 退出码 {rc}")
    return rc
