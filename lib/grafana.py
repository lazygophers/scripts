"""grafana — Grafana HTTP API 客户端：多域名 profile + token/basic 鉴权。"""

from __future__ import annotations

import base64
import json
import pathlib
import urllib.parse

from lib.profile_store import ProfileStore

CONFIG_PATH = pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / "grafana.yaml"
DEFAULT_TIMEOUT = 30


class GrafanaError(Exception):
    """请求失败 / 配置缺失。message 直接给用户看。"""


# ---------------------------------------------------------------- config

def default_config_path() -> pathlib.Path:
    return CONFIG_PATH


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


# 配置存储与 profile 解析都在 lib/profile_store.py（archery / email 共用同一份）。
# 这里保留模块级的函数名，调用点和测试照旧用 grafana.load_config(...) 这种写法。
_STORE = ProfileStore(
    "grafana.yaml", error=GrafanaError, key_fn=host_key,
    tool="grafana", path_resolver=default_config_path,
)
load_config = _STORE.load
save_config = _STORE.save
config_lock = _STORE.lock
profiles = _STORE.profiles
resolve_profile = _STORE.resolve
put_profile = _STORE.put


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
