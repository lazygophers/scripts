"""grafana — Grafana HTTP API 客户端：多域名 profile + token/basic 鉴权。"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import urllib.parse

CONFIG_PATH = pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / "grafana.yaml"
DEFAULT_TIMEOUT = 30


class GrafanaError(Exception):
    """请求失败 / 配置缺失。message 直接给用户看。"""


# ---------------------------------------------------------------- config

def default_config_path() -> pathlib.Path:
    return CONFIG_PATH


def load_config(path: pathlib.Path | None = None) -> dict:
    import yaml

    target = path or default_config_path()
    if not target.exists():
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_config(data: dict, path: pathlib.Path | None = None) -> None:
    import yaml

    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(target, 0o600)


@contextlib.contextmanager
def config_lock(path: pathlib.Path | None = None):
    import fcntl

    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target.with_name(f".{target.name}.lock")), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def normalize_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    parsed = urllib.parse.urlsplit(s)
    if not parsed.netloc:
        raise GrafanaError(f"看不懂的站点地址: {raw}")
    return f"{parsed.scheme}://{parsed.netloc}"


def host_key(raw: str) -> str:
    return urllib.parse.urlsplit(normalize_url(raw)).netloc.lower()


def profiles(cfg: dict) -> dict:
    got = cfg.get("profiles")
    return dict(got) if isinstance(got, dict) else {}


def resolve_profile(cfg: dict, host: str = "") -> tuple[str, dict]:
    all_p = profiles(cfg)
    if not all_p:
        raise GrafanaError(f"还没有配置任何站点（{CONFIG_PATH} 为空）。跑 `grafana login`")
    if host:
        key = host_key(host)
        if key not in all_p:
            known = ", ".join(sorted(all_p)) or "(无)"
            raise GrafanaError(f"没有这个站点的配置: {key}（已配置: {known}）。跑 `grafana login --url {host}`")
        return key, dict(all_p[key])
    current = str(cfg.get("current") or "")
    if current and current in all_p:
        return current, dict(all_p[current])
    if len(all_p) == 1:
        only = next(iter(all_p))
        return only, dict(all_p[only])
    known = ", ".join(sorted(all_p))
    raise GrafanaError(f"配了多个站点但没指定用哪个（{known}）。跑 `grafana use <域名>` 或加 --host")


def put_profile(cfg: dict, key: str, profile: dict) -> dict:
    all_p = profiles(cfg)
    all_p[key] = profile
    cfg["profiles"] = all_p
    if not cfg.get("current"):
        cfg["current"] = key
    return cfg


def parse_data(value) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("@"):
            path = pathlib.Path(s[1:]).expanduser()
            if not path.is_file():
                raise GrafanaError(f"读不到文件: {path}")
            s = path.read_text(encoding="utf-8")
        try:
            got = json.loads(s)
        except json.JSONDecodeError as e:
            raise GrafanaError(f"--data 不是合法 JSON: {e}") from e
        if not isinstance(got, dict):
            raise GrafanaError("--data 必须是 JSON 对象（{...}）")
        return got
    raise GrafanaError(f"--data 不支持的类型: {type(value).__name__}")


# ---------------------------------------------------------------- client

class GrafanaClient:
    """一个 Grafana 站点的 API 客户端。"""

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
        p = (path or "").strip()
        if p.startswith("http://") or p.startswith("https://"):
            return p
        if not p.startswith("/"):
            p = "/api/" + p
        return self.base_url + p

    def _headers(self) -> dict[str, str]:
        token = str(self.profile.get("token") or "")
        if token:
            return {"Authorization": f"Bearer {token}"}
        username = str(self.profile.get("username") or "")
        password = str(self.profile.get("password") or "")
        if username or password:
            raw = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        raise GrafanaError(f"{self.key} 缺 token 或 username/password。跑 `grafana login --url {self.key} --token <token>`")

    def request(self, method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
        url = self._url(path)
        try:
            resp = self.session.request(method.upper(), url, params=_clean(params), json=json_body,
                                        headers=self._headers(), timeout=self.timeout, verify=self.verify)
        except Exception as e:
            raise GrafanaError(f"{method.upper()} {url} 连不上: {e}") from e
        if resp.status_code >= 400:
            raise GrafanaError(f"{method.upper()} {url} -> HTTP {resp.status_code}: {_body_text(resp)}")
        return _body_json(resp)

    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None, **params):
        return self.request("POST", path, params=params, json_body=body or {})

    def health(self):
        return self.get("/api/health")

    def search(self, query: str):
        return self.get("/api/search", query=query)


def client_for(host: str = "", *, reporter=None,
               config_path: pathlib.Path | None = None,
               timeout: int = DEFAULT_TIMEOUT) -> GrafanaClient:
    cfg = load_config(config_path)
    key, profile = resolve_profile(cfg, host)
    return GrafanaClient(key, profile, cfg, config_path=config_path,
                         timeout=timeout, reporter=reporter)


# ---------------------------------------------------------------- helpers

def _clean(params: dict | None) -> dict:
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
