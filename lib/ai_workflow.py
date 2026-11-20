"""AI workflow 共享工具：git remote/provider 检测 + LAZYGOPHERS API 文本生成。

commit / mr / issue 三个脚本的公共逻辑。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lib.ai_env import json_dumps
from lib.exec import NET_TIMEOUT, retry_command, run
from lib.ui import reporter

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


_API_ENV = ("LAZYGOPHERS_SCRIPTS_BASE_URL", "LAZYGOPHERS_SCRIPTS_TOKEN")


def _lazygophers_enabled() -> bool:
    """LAZYGOPHERS_SCRIPTS_BASE_URL + _TOKEN 均存在时启用 API 路径。"""
    return all(os.environ.get(k) for k in _API_ENV)


def generate_via_lazygophers(prompt: str, *, system_prompt: str,
                             max_tokens: int = 300, timeout: float = 30.0,
                             extract: Callable[[list[str]], str] = "\n".join) -> str:
    """调 LAZYGOPHERS /chat/compate（Anthropic 风格式）生成文本。

    请求：POST {BASE_URL}/chat/compate，body {model, max_tokens, system, messages}，
    鉴权 Authorization: Bearer <token>，响应遵循 Anthropic Messages content/stop_reason。
    对 thinking-only 或 max_tokens 响应最多续接两轮，保留完整 assistant content。
    extract 从全部 text block 里取结果（commit 用它剥思考散文）。
    Returns: 生成文本（strip）。env 缺失或失败返回空串（错误已打印）。
    """
    r = reporter(stderr=True)
    if not _lazygophers_enabled():
        r.err(f"未配置 {' / '.join(_API_ENV)}，无法生成文本（运行 inject 配置）")
        return ""
    base = os.environ["LAZYGOPHERS_SCRIPTS_BASE_URL"].rstrip("/")
    token = os.environ["LAZYGOPHERS_SCRIPTS_TOKEN"]
    url = f"{base}/chat/compate"
    project_root = Path(__file__).resolve().parent.parent
    version = run(["git", "rev-parse", "HEAD"], check=False,
                  capture_output=True, cwd=project_root)
    commit_hash = (version.stdout or "").strip()
    if version.returncode != 0 or not commit_hash:
        r.err("无法读取 scripts 工具的 commit hash")
        return ""
    project_name = "lazyscripts"

    payload = {
        "model": "haiku",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }
    texts = []
    try:
        for _ in range(3):
            body = json.dumps(payload, ensure_ascii=False).encode()
            req = urllib.request.Request(url, data=body, headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
                "user-agent": f"{project_name}/{commit_hash}",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                _debug_dump(url, payload, raw)
                data = json.loads(raw)
            parts = data.get("content") or []
            texts.extend(p.get("text", "") for p in parts if p.get("type") == "text")
            if data.get("stop_reason") != "max_tokens" and texts:
                break
            payload["messages"].extend([
                {"role": "assistant", "content": parts},
                {"role": "user", "content":
                    "请从上次 assistant 响应停止处继续，直接续写，不要重复已有内容；只输出要求的结果。"},
            ])
        if data.get("stop_reason") == "max_tokens":
            r.err("LAZYGOPHERS API 连续 3 轮达到 max_tokens，未生成完整结果")
            return ""
        return extract(texts).strip()
    except urllib.error.HTTPError as e:
        # HTTP 错误：打印状态码 + 原因（已 strip），不暴露 traceback/异常类名
        r.err(f"LAZYGOPHERS API 返回 HTTP {e.code}（{e.reason}）")
        return ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 网络层错误（连接拒绝/超时/DNS 等）：不打印异常 repr（含 errno/类名），
        # 只输出可读中文，避免像堆栈
        reason = getattr(e, "reason", None) or e
        # OSError 子类（ConnectionRefusedError 等）优先取 strerror（纯文案，
        # 无 errno 数字）；普通 OSError / TimeoutError 取 strerror；
        # 其他（reason 为字符串等）直接 str()
        text = getattr(reason, "strerror", None) or getattr(e, "strerror", None)
        if not text:
            text = str(reason)
        r.err(f"LAZYGOPHERS API 连接失败：{text}")
        return ""
    except (ValueError, KeyError, IndexError):
        # JSON 解析 / 字段缺失：不暴露异常类名
        r.err("LAZYGOPHERS API 响应格式异常")
        return ""


def _debug_dump(url: str, payload: dict, raw: bytes | None = None) -> None:
    """--debug 打印 LAZYGOPHERS 原始请求 body + 响应 body 到 stderr。"""
    from lib.notify import is_debug
    if not is_debug():
        return
    rr = reporter(stderr=True)
    rr.step(f"[debug] LAZYGOPHERS POST {url}")
    rr.step("[debug] request body:")
    rr.output(json_dumps(payload), force=True)
    rr.step("[debug] response body:")
    rr.output(raw.decode("utf-8", "replace") if raw else "(无)", force=True)


def split_title_body(text: str) -> tuple[str, str]:
    """把「首行 title + 空行 + body」拆成 (title, body)。

    防御模型的常见偏差：外层代码块围栏、title 前的 `#` / `Title:` / `标题：` 前缀和引号。
    """
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    title = re.sub(r"^(?:#+\s*|(?:title|标题)\s*[:：]\s*)", "", lines[0].strip(), flags=re.I)
    return title.strip().strip("\"'“”「」").strip(), "\n".join(lines[1:]).strip()


def create_with_generated_text(
    prompt: str, *, system_prompt: str,
    build_argv: Callable[[str, str], list[str]],
) -> int:
    """API 生成 title/body → build_argv(title, body) 得 gh/glab 命令 → 执行（网络错误重试一次）。

    失败（env 缺失 / API 失败 / 无 title / 命令失败）返回 1，命令输出原样透传。
    """
    r = reporter(stderr=True)
    text = generate_via_lazygophers(prompt, system_prompt=system_prompt, max_tokens=2000)
    if not text:
        r.err("生成 title/body 失败，已取消创建")
        return 1
    title, body = split_title_body(text)
    if not title:
        r.err(f"生成结果缺少 title，已取消创建：{text}")
        return 1
    argv = build_argv(title, body)
    res = retry_command(argv, max_retries=1, timeout=NET_TIMEOUT)
    out = res.last_output.strip()
    if not res.ok:
        r.err(f"{' '.join(argv[:3])} 失败：{out}")
        return 1
    print(out)
    return 0


