"""browse 安全层：确认策略 + 域名拒绝名单 + 审计日志（spec 4.4 / 4.6）。

纯策略，不碰 socket 也不碰浏览器。daemon 在把指令转给浏览器之前问一次
`Security.check()`，拿到结果后再问一次 `Security.audit()` 记账。

三条不能省的性质：

- **命令行只能收紧不能放宽**：配置写 `always`，单次调用传 `silent` 直接报错。
  否则「被入侵的脚本自己把确认关掉」这条路是敞开的 —— 攻击者已经能执行代码，
  拦不住他连 socket，但不能让他一行参数就把用户的确认策略清零。
- **审计只记「对哪个域名做了什么」**：不记页面正文、不记 cookie 值、不记表单输入。
- **写盘前先脱敏再截断**（`lib/browse_redact.clean`），顺序见那个模块的说明。

配置落 `~/.config/lazygophers/scripts/browse.yaml`（0600），字段：

    confirm_mode: silent            # silent | per_domain | always
    deny_domains: []                # 命中即拒绝，默认空
    approved_domains: []            # per_domain 模式下已免确认的域名，可撤销
    audit: true                     # 关掉就不写审计文件
    audit_retention_days: 7         # 超期的 audit-*.jsonl 自动删，<=0 表示不删

文件读写复用 `lib/profile_store.ProfileStore` 的 load / save / lock ——
要的就是它那三条性质（原子换名、0600、跨进程 flock）。它的 profile 解析
（`resolve` / `put` / `key_fn`）用不上：browse 的配置是单份全局配置，不是
按站点分 profile，所以那几个方法这里一次都不调。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from lib.browse_protocol import ERR_USER_REJECTED
from lib.browse_redact import clean
from lib.profile_store import ProfileStore

# ---------------------------------------------------------------- 确认模式

# 由松到紧。命令行只能往后挪，不能往前挪。
CONFIRM_MODES = ("silent", "per_domain", "always")
DEFAULT_CONFIRM_MODE = "silent"

DEFAULT_RETENTION_DAYS = 7


class SecurityError(Exception):
    """策略拒绝或配置非法。`code` 直接就是给 CLI 的错误码。"""

    def __init__(self, message: str, code: str = ERR_USER_REJECTED):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- 高危动作

# 方法名 → 动作名，与扩展侧 `RiskyAction`
# （`browser-extension/extension/src/handlers/confirm.ts:14-24`）逐字对齐。
RISKY_METHODS: dict[str, str] = {
    "storage.getCookies": "readCookies",
    "storage.setCookie": "writeCookies",
    "storage.deleteCookies": "writeCookies",
    "storage.getLocalStorage": "readLocalStorage",
    "storage.setLocalStorage": "writeLocalStorage",
    "script.evaluate": "evalMainWorld",
    "script.callFunction": "evalMainWorld",
    "lg:downloads.start": "download",
    "lg:history.search": "readHistory",
    "lg:history.delete": "writeHistory",
    "lg:bookmarks.search": "readBookmarks",
    "lg:bookmarks.create": "writeBookmarks",
    "lg:bookmarks.remove": "writeBookmarks",
}

RISKY_ACTIONS = frozenset(RISKY_METHODS.values())


def risky_action(method: str, params: dict | None = None) -> str | None:
    """这条指令算高危动作吗，算就返回动作名。

    `js=` 定位器在 MAIN world 求值（`handlers/input.ts:88`），spec 4.4 点名它也是
    高危，所以 `input.*` 带 `js=` 的一样要确认 —— 光看方法名会漏。

    **字段名是 `selector`**，线上就叫这个（CLI 的位置参数名见
    `lib/cli/browse.py:82`，扩展侧读的是 `params.selector`，`handlers/input.ts:65`）。
    """
    if method in RISKY_METHODS:
        return RISKY_METHODS[method]
    selector = (params or {}).get("selector")
    if isinstance(selector, str) and selector.startswith("js="):
        return "evalMainWorld"
    return None


# 这些指令冲着「某个页面」去，但 params 里没有 url / domain —— 目标页是扩展按
# context > matchUrl > 当前活动标签页算出来的（`handlers/context.ts:41`）。deny_domains
# 要拦得住它们，daemon 就得先问扩展一句「这条指令落在哪个页面上」，见
# `browse_daemon.Daemon._gate`。清单与扩展侧调 `resolveContext` 的 handler 一一对应。
PAGE_METHODS = frozenset({
    "script.evaluate",
    "script.callFunction",
    "input.click",
    "input.type",
    "input.key",
    "input.scroll",
    "storage.getLocalStorage",
    "storage.setLocalStorage",
    "browsingContext.close",
    "browsingContext.activate",
    "browsingContext.reload",
    "browsingContext.captureScreenshot",
    "lg:page.snapshot",
})


def target_url(params: dict | None) -> str | None:
    """从 params 里挑出这条指令冲着哪个页面/域名去。

    cookie 类指令给的是 `domain`（没有 scheme），页面类给的是 `url`，
    浏览器全局的（列书签、搜历史）两个都没有 —— 返回 None。
    """
    params = params or {}
    for key in ("url", "domain"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def domain_of(url: str | None) -> str | None:
    """URL / 裸域名 → 小写主机名，取不出来返回 None。"""
    if not url:
        return None
    host = urlsplit(url).hostname
    if not host:
        # cookie 过滤器给的是裸域名（可能带前导点），urlsplit 认不出来
        host = url.split("/")[0].split(":")[0]
    host = host.strip().lstrip(".").lower()
    return host or None


# ---------------------------------------------------------------- 配置

# key_fn / error 只为满足构造签名：browse 不按 key 分 profile，resolve/put 不会被调
_STORE = ProfileStore(
    "browse.yaml",
    error=SecurityError,
    key_fn=lambda value: str(value).strip().lower(),
    tool="browse",
    noun="配置",
)

load_config = _STORE.load
save_config = _STORE.save
config_lock = _STORE.lock
default_config_path = _STORE.default_path


def resolve_confirm_mode(configured: str | None, override: str | None = None) -> str:
    """配置里的模式 + 命令行覆盖 → 实际生效的模式。**只能收紧不能放宽。**"""
    mode = (configured or DEFAULT_CONFIRM_MODE).strip() or DEFAULT_CONFIRM_MODE
    if mode not in CONFIRM_MODES:
        raise SecurityError(
            f"confirm_mode 非法: {mode!r}（可选 {' / '.join(CONFIRM_MODES)}）",
            code="invalid argument",
        )
    override = (override or "").strip()
    if not override:
        return mode
    if override not in CONFIRM_MODES:
        raise SecurityError(
            f"--confirm-mode 非法: {override!r}（可选 {' / '.join(CONFIRM_MODES)}）",
            code="invalid argument",
        )
    if CONFIRM_MODES.index(override) < CONFIRM_MODES.index(mode):
        raise SecurityError(
            f"--confirm-mode {override} 比配置里的 {mode} 松，命令行只能收紧不能放宽。"
            f"要放宽请改 {default_config_path()}",
            code="invalid argument",
        )
    return override


def domain_matches(domain: str | None, pattern: str) -> bool:
    """域名匹配：写 `example.com` 或 `*.example.com` 都覆盖它本身和全部子域。

    拒绝名单上少匹配一条就是漏一个，所以两种写法都取宽的那个语义。
    """
    if not domain:
        return False
    pattern = pattern.strip().lstrip("*").lstrip(".").lower()
    if not pattern:
        return False
    return domain == pattern or domain.endswith("." + pattern)


# ---------------------------------------------------------------- 审计路径


def state_dir() -> Path:
    """审计文件所在目录（与 daemon 的 socket 回落位置同根）。"""
    return Path.home() / ".local" / "state" / "lazygophers" / "scripts" / "browse"


class Security:
    """一份加载好的策略 + 它的审计文件。daemon 每个进程建一个。"""

    def __init__(self, cfg: dict | None = None, *, override_mode: str = "",
                 config_path: Path | None = None, audit_dir: Path | None = None):
        self.config_path = config_path
        self.cfg = load_config(config_path) if cfg is None else dict(cfg)
        self.confirm_mode = resolve_confirm_mode(self.cfg.get("confirm_mode"), override_mode)
        self.audit_dir = audit_dir or state_dir()
        self._pruned = False

    # -------------------------------------------------------- 名单

    def _list(self, key: str) -> list[str]:
        got = self.cfg.get(key)
        return [str(x) for x in got] if isinstance(got, list) else []

    @property
    def deny_domains(self) -> list[str]:
        return self._list("deny_domains")

    def approvals(self) -> list[str]:
        """per_domain 模式下已免确认的域名。"""
        return self._list("approved_domains")

    def approve(self, domain: str) -> None:
        """记下「这个域名以后免确认」并落盘。"""
        self._update_approvals(lambda got: got if domain in got else got + [domain])

    def revoke(self, domain: str) -> None:
        """撤销一条免确认记录。"""
        self._update_approvals(lambda got: [d for d in got if d != domain])

    def _update_approvals(self, change) -> None:
        # 读—改—写全程持锁并从盘上重读：另一个 browse 进程可能正在改同一份配置
        with config_lock(self.config_path):
            cfg = load_config(self.config_path)
            got = cfg.get("approved_domains")
            got = [str(x) for x in got] if isinstance(got, list) else []
            cfg["approved_domains"] = change(got)
            save_config(cfg, self.config_path)
        self.cfg["approved_domains"] = cfg["approved_domains"]

    # -------------------------------------------------------- 决策

    def needs_target_lookup(self, method: str, params: dict | None = None,
                            mode: str | None = None) -> bool:
        """要不要先问扩展「这条指令落在哪个页面上」。

        两种情况才值得多花这个往返：

        - **拒绝名单非空**：不知道目标页就没法判 deny_domains（判定结果见调用方，
          问不到时 fail closed）。名单空着就没什么可拦。
        - **这条指令要弹确认**：确认框上得写清楚是哪个站，否则用户在给一个没有主语的
          请求点同意。顺带一件事：扩展侧的去抖缓存按 `动作+方法+URL` 做键，daemon 这边
          留 `None` 而扩展那边填真实 URL，同一个问题会被当成两个，弹两次框。
        """
        if method not in PAGE_METHODS or target_url(params) is not None:
            return False
        if self.deny_domains:
            return True
        mode = self.confirm_mode if mode is None else mode
        return mode != "silent" and risky_action(method, params) is not None

    def check(self, method: str, params: dict | None = None, *,
              url: str | None = None, mode: str | None = None) -> dict | None:
        """放行返回 None；要弹确认返回 ConfirmRequest；命中拒绝名单直接抛。

        `url` 是调用方已经替这条指令解析出来的目标页（`needs_target_lookup` 为真时
        daemon 会去问扩展）；不给就从 params 里取。`mode` 是这一次调用生效的确认模式，
        不给就用加载时算好的那个。

        ConfirmRequest 的形状与扩展侧
        （`handlers/confirm.ts:26-32`）一致：`{action, method, url}`。
        """
        url = target_url(params) if url is None else url
        domain = domain_of(url)
        for pattern in self.deny_domains:
            if domain_matches(domain, pattern):
                raise SecurityError(f"{domain} 命中拒绝名单（deny_domains: {pattern}）")

        mode = self.confirm_mode if mode is None else mode
        action = risky_action(method, params)
        if action is None or mode == "silent":
            return None
        if mode == "per_domain" and domain and domain in self.approvals():
            return None
        return {"action": action, "method": method, "url": url}

    # -------------------------------------------------------- 审计

    def audit_path(self, when: datetime.date | None = None) -> Path:
        day = when or datetime.date.today()
        return self.audit_dir / f"audit-{day.isoformat()}.jsonl"

    def audit(self, method: str, *, params: dict | None = None, pid: int | None = None,
              result: str = "success", elapsed_ms: float | None = None,
              error: str | None = None) -> Path | None:
        """记一行。只记「对哪个域名做了什么动作」，不记内容。"""
        if self.cfg.get("audit") is False:
            return None
        entry = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "method": method,
            "domain": domain_of(target_url(params)),
            "action": risky_action(method, params),
            "pid": os.getpid() if pid is None else pid,
            "result": result,
            "ms": None if elapsed_ms is None else round(elapsed_ms, 1),
        }
        if error:
            entry["error"] = error
        # clean 先脱敏再截断；params 本身一个字都不进来
        line = json.dumps(clean(entry), ensure_ascii=False) + "\n"

        self.audit_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.audit_dir, 0o700)
        path = self.audit_path()
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)

        if not self._pruned:
            self._pruned = True
            self.prune()
        return path

    def prune(self, today: datetime.date | None = None) -> list[Path]:
        """删掉超过保留期的审计文件，返回删掉的那些。"""
        try:
            days = int(self.cfg.get("audit_retention_days", DEFAULT_RETENTION_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        if days <= 0:
            return []
        cutoff = (today or datetime.date.today()) - datetime.timedelta(days=days)
        removed = []
        for path in sorted(self.audit_dir.glob("audit-*.jsonl")):
            try:
                day = datetime.date.fromisoformat(path.stem[len("audit-"):])
            except ValueError:
                continue  # 不是我们写的文件，不碰
            if day < cutoff:
                path.unlink()
                removed.append(path)
        return removed
