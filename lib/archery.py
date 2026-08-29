"""archery — Archery(hhyo/archery) 的 HTTP 客户端：多域名 profile + JWT 自动续期。

认证走 Archery 自带的 SimpleJWT 端点（见上游 sql_api/urls.py）：
  POST /api/auth/token/          {username, password} -> {access, refresh}
  POST /api/auth/token/refresh/  {refresh}            -> {access}
  POST /api/auth/token/verify/   {token}
access 短命，请求收到 401 时先用 refresh 换新的；refresh 也失效则用配置里的
用户名密码重新登录，全程无需用户介入。

配置文件 ~/.config/lazygophers/scripts/archery.yaml（0600）按域名分 profile：

    current: archery.example.com
    profiles:
      archery.example.com:
        url: https://archery.example.com
        username: nico
        password: ...
        totp_secret: ...      # 可选，只给 `archery code` / 2fa 子命令用
        insecure: false       # true = 跳过 TLS 证书校验（自签证书内网用）
        token: {access: ..., refresh: ...}

同一台机器上可以同时配多个 Archery 站点，`current` 是默认那个，命令行
`--host` 可以临时指向另一个（写域名或完整 URL 都行）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse

from lib.ovpn import load_config as _load_yaml
from lib.ovpn import save_config as _save_yaml

CONFIG_PATH = pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / "archery.yaml"

# JWT 端点（不带 /api 前缀，_url() 会补）
TOKEN_PATH = "/api/auth/token/"
TOKEN_REFRESH_PATH = "/api/auth/token/refresh/"
TOKEN_VERIFY_PATH = "/api/auth/token/verify/"

DEFAULT_TIMEOUT = 30


class ArcheryError(Exception):
    """请求失败 / 配置缺失。message 直接给用户看。"""


# ---------------------------------------------------------------- config

def load_config(path: pathlib.Path | None = None) -> dict:
    """读配置；文件不存在返回空 dict。不给路径时按当前身份推断（见 default_config_path）。"""
    return _load_yaml(path or default_config_path())


def save_config(data: dict, path: pathlib.Path | None = None) -> None:
    """写配置，权限 0600（里面有明文密码和 TOTP 密钥）。"""
    _save_yaml(data, path or default_config_path())


def normalize_url(raw: str) -> str:
    """把用户输入的站点地址补成 `scheme://host[:port]`，去掉末尾斜杠与路径。

    'archery.example.com' -> 'https://archery.example.com'
    'http://10.0.0.1:9123/' -> 'http://10.0.0.1:9123'
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    parsed = urllib.parse.urlsplit(s)
    if not parsed.netloc:
        raise ArcheryError(f"看不懂的站点地址: {raw}")
    return f"{parsed.scheme}://{parsed.netloc}"


def host_key(raw: str) -> str:
    """profile 的 key：域名（含端口，不含 scheme），小写。"""
    return urllib.parse.urlsplit(normalize_url(raw)).netloc.lower()


def profiles(cfg: dict) -> dict:
    """配置里的所有 profile，key 是域名。"""
    got = cfg.get("profiles")
    return dict(got) if isinstance(got, dict) else {}


def resolve_profile(cfg: dict, host: str = "") -> tuple[str, dict]:
    """挑出要用的 profile：显式 --host 优先，其次 current，只有一个时直接用它。

    返回 (key, profile)。找不到时抛 ArcheryError，消息里带下一步动作。
    """
    all_p = profiles(cfg)
    if not all_p:
        raise ArcheryError(f"还没有配置任何站点（{CONFIG_PATH} 为空）。跑 `archery login`")
    if host:
        key = host_key(host)
        if key not in all_p:
            known = ", ".join(sorted(all_p)) or "(无)"
            raise ArcheryError(f"没有这个站点的配置: {key}（已配置: {known}）。跑 `archery login --url {host}`")
        return key, dict(all_p[key])
    current = str(cfg.get("current") or "")
    if current and current in all_p:
        return current, dict(all_p[current])
    if len(all_p) == 1:
        only = next(iter(all_p))
        return only, dict(all_p[only])
    known = ", ".join(sorted(all_p))
    raise ArcheryError(f"配了多个站点但没指定用哪个（{known}）。跑 `archery use <域名>` 或加 --host")


def put_profile(cfg: dict, key: str, profile: dict) -> dict:
    """把 profile 写回 cfg（不落盘），没有 current 时顺手设成它。"""
    all_p = profiles(cfg)
    all_p[key] = profile
    cfg["profiles"] = all_p
    if not cfg.get("current"):
        cfg["current"] = key
    return cfg


def mask(value: str) -> str:
    """密码 / 密钥打码，两头留 2 位。"""
    if not value:
        return "(未设置)"
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


# ---------------------------------------------------------------- 提权

def is_root() -> bool:
    """当前进程是不是 root（euid 0）。"""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def default_config_path() -> pathlib.Path:
    """配置文件路径，sudo 下回落到发起 sudo 的那个用户的家目录。

    sudo 默认把 `$HOME` 换成 /var/root，直接敲 `sudo archery show` 就会读不到你
    自己的配置。有 `SUDO_USER` 时按那个用户的家目录找，文件真的在才用它。
    """
    if is_root():
        sudo_user = os.environ.get("SUDO_USER") or ""
        if sudo_user:
            import pwd

            try:
                home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir)
            except KeyError:
                return CONFIG_PATH
            candidate = home / ".config" / "lazygophers" / "scripts" / "archery.yaml"
            if candidate.exists():
                return candidate
    return CONFIG_PATH


def sudo_argv(script: pathlib.Path, argv: list[str], config_path: pathlib.Path) -> list[str]:
    """拼出用 sudo 重跑自己的命令行。

    sudo 默认 env_reset，`$HOME` 到了 root 手里就变成 /var/root，配置文件会找不着，
    所以把当前解析出来的配置路径显式当成 `--config` 参数带过去（已经带了就不重复加）。
    解释器写成绝对路径（sys.executable），避免 sudo 的 PATH 里没有 mise/venv 的 python。
    """
    args = list(argv)
    if "--config" not in args:
        args += ["--config", str(config_path)]
    return ["sudo", sys.executable, str(script), *args]


def require_root(script: pathlib.Path, argv: list[str], config_path: pathlib.Path) -> None:
    """密钥类命令的门槛：不是 root 就用 sudo 原样重跑自己（execvp，不返回）。"""
    if is_root():
        return
    cmd = sudo_argv(script, argv, config_path)
    try:
        os.execvp("sudo", cmd)
    except OSError as e:
        raise ArcheryError(f"这条命令需要 root，但起不了 sudo: {e}") from e


def parse_data(value) -> dict:
    """把 CLI 传来的 --data 归一成 dict。

    支持三种写法：fire 已经解析好的 dict、JSON 字符串、`@path/to.json` 读文件。
    None / 空 -> {}。
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("@"):
            path = pathlib.Path(s[1:]).expanduser()
            if not path.is_file():
                raise ArcheryError(f"读不到文件: {path}")
            s = path.read_text(encoding="utf-8")
        try:
            got = json.loads(s)
        except json.JSONDecodeError as e:
            raise ArcheryError(f"--data 不是合法 JSON: {e}") from e
        if not isinstance(got, dict):
            raise ArcheryError("--data 必须是 JSON 对象（{...}）")
        return got
    raise ArcheryError(f"--data 不支持的类型: {type(value).__name__}")


# ---------------------------------------------------------------- client

class ArcheryClient:
    """一个站点的 API 客户端。token 变化时自动写回配置文件。"""

    def __init__(self, key: str, profile: dict, cfg: dict | None = None, *,
                 config_path: pathlib.Path | None = None,
                 timeout: int = DEFAULT_TIMEOUT, reporter=None) -> None:
        self.key = key
        self.profile = dict(profile)
        self.cfg = cfg if cfg is not None else {}
        self.config_path = config_path or default_config_path()
        self.timeout = timeout
        self._r = reporter
        self.base_url = normalize_url(str(self.profile.get("url") or key))
        self._session = None

    # -------------------------------------------------------- 内部

    @property
    def session(self):
        import requests

        if self._session is None:
            self._session = requests.Session()
        return self._session

    @property
    def verify(self) -> bool:
        return not bool(self.profile.get("insecure"))

    def _url(self, path: str) -> str:
        """path 三种写法：完整 URL / `/api/...` 绝对路径 / `v1/user/` 简写（自动补 /api/）。"""
        p = (path or "").strip()
        if p.startswith("http://") or p.startswith("https://"):
            return p
        if not p.startswith("/"):
            p = "/api/" + p
        return self.base_url + p

    def _token(self, name: str) -> str:
        token = self.profile.get("token") or {}
        return str(token.get(name) or "")

    def _store_token(self, access: str, refresh: str = "") -> None:
        token = dict(self.profile.get("token") or {})
        token["access"] = access
        if refresh:
            token["refresh"] = refresh
        self.profile["token"] = token
        put_profile(self.cfg, self.key, self.profile)
        save_config(self.cfg, self.config_path)

    def _post_json(self, path: str, payload: dict):
        """走 _raw，网络异常同样转成 ArcheryError（而不是甩 traceback）。"""
        return self._raw("POST", self._url(path), params=None, json_body=payload, headers={})

    # -------------------------------------------------------- 认证

    def login(self) -> str:
        """用配置里的用户名密码换一对新 token，写回配置，返回 access。"""
        username = str(self.profile.get("username") or "")
        password = str(self.profile.get("password") or "")
        if not username or not password:
            raise ArcheryError(f"{self.key} 缺用户名或密码。跑 `archery login --url {self.key}`")
        resp = self._post_json(TOKEN_PATH, {"username": username, "password": password})
        if resp.status_code != 200:
            raise ArcheryError(f"登录失败 HTTP {resp.status_code}: {_body_text(resp)}")
        data = resp.json()
        access, refresh = str(data.get("access") or ""), str(data.get("refresh") or "")
        if not access:
            raise ArcheryError(f"登录响应里没有 access token: {data}")
        self._store_token(access, refresh)
        return access

    def refresh_token(self) -> str:
        """用 refresh 换新 access；refresh 失效时回落到重新登录。"""
        refresh = self._token("refresh")
        if refresh:
            resp = self._post_json(TOKEN_REFRESH_PATH, {"refresh": refresh})
            if resp.status_code == 200:
                data = resp.json()
                access = str(data.get("access") or "")
                if access:
                    # refresh 轮换（ROTATE_REFRESH_TOKENS 开启时响应会带新 refresh）
                    self._store_token(access, str(data.get("refresh") or ""))
                    return access
        return self.login()

    def verify_token(self) -> bool:
        """问服务端当前 access 还有效没有（不刷新、不改配置）。"""
        access = self._token("access")
        if not access:
            return False
        return self._post_json(TOKEN_VERIFY_PATH, {"token": access}).status_code == 200

    # -------------------------------------------------------- 请求

    def request(self, method: str, path: str, *, params: dict | None = None,
                json_body: dict | None = None, auth: bool = True):
        """发一个请求，返回解析后的 JSON（无 body 时返回 None）。

        401 时自动续 token 重试一次；4xx/5xx 抛 ArcheryError（消息带响应体）。
        """
        url = self._url(path)
        headers = {}
        if auth:
            access = self._token("access") or self.login()
            headers["Authorization"] = f"Bearer {access}"

        resp = self._raw(method, url, params=params, json_body=json_body, headers=headers)
        if auth and resp.status_code == 401:
            headers["Authorization"] = f"Bearer {self.refresh_token()}"
            resp = self._raw(method, url, params=params, json_body=json_body, headers=headers)

        if resp.status_code >= 400:
            raise ArcheryError(f"{method.upper()} {url} -> HTTP {resp.status_code}: {_body_text(resp)}")
        return _body_json(resp)

    def _raw(self, method: str, url: str, *, params, json_body, headers):
        import requests

        try:
            return self.session.request(
                method.upper(), url, params=params, json=json_body,
                headers=headers, timeout=self.timeout, verify=self.verify,
            )
        except requests.RequestException as e:
            raise ArcheryError(f"{method.upper()} {url} 连不上: {e}") from e

    # -------------------------------------------------------- 便捷方法

    def get(self, path: str, **params):
        return self.request("GET", path, params=_clean(params))

    def post(self, path: str, body: dict | None = None, **params):
        return self.request("POST", path, params=_clean(params) or None, json_body=body or {})

    def put(self, path: str, body: dict | None = None):
        return self.request("PUT", path, json_body=body or {})

    def delete(self, path: str):
        return self.request("DELETE", path)


def client_for(host: str = "", *, reporter=None,
               config_path: pathlib.Path | None = None,
               timeout: int = DEFAULT_TIMEOUT) -> ArcheryClient:
    """按 --host / current 挑 profile 并造客户端。"""
    cfg = load_config(config_path)
    key, profile = resolve_profile(cfg, host)
    return ArcheryClient(key, profile, cfg, config_path=config_path,
                         timeout=timeout, reporter=reporter)


# ---------------------------------------------------------------- helpers

def _clean(params: dict) -> dict:
    """去掉值为 None / 空串的查询参数，避免把默认值当过滤条件发出去。"""
    return {k: v for k, v in (params or {}).items() if v is not None and v != ""}


def _body_text(resp) -> str:
    text = (resp.text or "").strip()
    return text[:800] if text else "(空响应体)"


def _body_json(resp):
    if resp.status_code == 204 or not (resp.content or b"").strip():
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def flatten_schema(schema: dict) -> list[tuple[str, str, str]]:
    """把 OpenAPI schema 压成 [(METHOD, path, summary)]，按 path 排序。

    用于 `archery schema`：列出这个站点实际支持的全部端点。
    """
    rows: list[tuple[str, str, str]] = []
    paths = schema.get("paths") if isinstance(schema, dict) else None
    for path, ops in sorted((paths or {}).items()):
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            summary = ""
            if isinstance(op, dict):
                head = str(op.get("summary") or op.get("description") or "").strip().splitlines()
                summary = head[0] if head else ""
            rows.append((method.upper(), path, summary))
    return rows
