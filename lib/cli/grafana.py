"""grafana — Grafana HTTP API 命令行客户端，按域名分别保存登录。"""

from __future__ import annotations

import json
import sys
from functools import wraps

from lib.ai_env import json_dumps
from lib.fire_base import run_cli, timed_cli
from lib.grafana import (
    GrafanaError,
    client_for,
    config_lock,
    default_config_path,
    host_key,
    load_config,
    normalize_url,
    parse_data,
    profiles,
    put_profile,
    resolve_profile,
    save_config,
)
from lib.ui import Reporter, ask_text, reporter, timed


def _print_help() -> None:
    r = reporter(stderr=True)
    r.rule("grafana", style="blue")
    r.step("Grafana HTTP API 客户端")
    r.step("用法: grafana <command> [flags]")
    r.summary("常用", [
        ("login", "录入一个 Grafana 站点", "blue"),
        ("hosts", "列出已配置站点", "blue"),
        ("health", "查看 Grafana 健康状态", "blue"),
        ("search", "搜索仪表盘", "blue"),
        ("logs", "查 Loki 日志最新 n 行", "blue"),
        ("api", "直接调用任意 Grafana API", "blue"),
    ])
    r.step("提示: 裸跑 `grafana` 会显示 `--skills`。")


def emit(data) -> None:
    if data is None:
        return
    if isinstance(data, str):
        print(data)
    else:
        print(json_dumps(data))


def cmd(method):
    @timed_cli
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except GrafanaError as e:
            self._r.err(str(e))
            return 2

    return wrapper


class _Group:
    def __init__(self, reporter_obj: Reporter | None = None) -> None:
        self._r = reporter_obj or reporter(stderr=True)

    def _client(self, host: str = ""):
        return client_for(host, reporter=self._r)


class AuthCli(_Group):
    """认证状态"""

    @cmd
    def state(self, host: str = ""):
        """查看当前 token 是否可用。"""
        emit(self._client(host).health())
        return 0


class GrafanaCli(_Group):
    """Grafana HTTP API 客户端"""

    def __init__(self, reporter_obj: Reporter | None = None) -> None:
        super().__init__(reporter_obj)
        self.auth = AuthCli(self._r)

    @cmd
    def hosts(self, host: str = ""):
        """列出已配置站点。"""
        cfg = load_config()
        known = profiles(cfg)
        if not known:
            self._r.warn(f"还没有配置任何站点（{default_config_path()}）。跑 `grafana login`")
            return 1
        current = str(cfg.get("current") or "")
        for name, profile in sorted(known.items()):
            marker = "★" if name == current else " "
            self._r.step(f"{marker} {name} -> {profile.get('url') or name}")
        return 0

    @cmd
    def use(self, host: str):
        """切换默认站点。"""
        with config_lock():
            cfg = load_config()
            key, profile = resolve_profile(cfg, host)
            cfg["current"] = key
            cfg = put_profile(cfg, key, profile)
            save_config(cfg)
        self._r.ok(f"已切换到 {host_key(host)}")
        return 0

    @cmd
    def login(self, url: str = "", token: str = "", username: str = "", password: str = "",
              insecure: bool = False, host: str = ""):
        """录入一个 Grafana 站点。"""
        if host:
            url = host
        if not url:
            url = ask_text("Grafana 站点地址", default="") or ""
            if not url.strip():
                self._r.err("已取消")
                return 1
        if not token and not username:
            token = ask_text("Grafana token（没有就回车用用户名密码）", default="") or ""
        if not token and not username:
            username = ask_text("Grafana 用户名", default="") or ""
            if not username.strip():
                self._r.err("已取消")
                return 1
        if not token and not password:
            password = ask_text("Grafana 密码", default="") or ""
            if not password:
                self._r.err("已取消")
                return 1
        profile = {
            "url": normalize_url(url),
            "token": token,
            "username": username,
            "password": password,
            "insecure": bool(insecure),
        }
        key = host_key(url)
        with config_lock():
            cfg = load_config()
            cfg = put_profile(cfg, key, profile)
            cfg["current"] = key
            save_config(cfg)
        self._r.ok(f"已保存 {key}")
        return 0

    @cmd
    def health(self, host: str = ""):
        """查看 Grafana 健康状态。"""
        emit(self._client(host).health())
        return 0

    @cmd
    def search(self, query: str, host: str = ""):
        """搜索仪表盘。"""
        emit(self._client(host).search(query))
        return 0

    @cmd
    def logs(self, selector: str, filter=(), n: int = 10, since: str = "24h",
             datasource: str = "", host: str = ""):
        """查 Loki 日志最新 n 行。

        selector: Loki 标签选择器，如 '{service_name="my-service"}'
        filter: 行内必须包含的关键词，可多次传
        since: 时间窗口，如 30m / 24h / 7d
        datasource: Loki datasource uid，缺省自动找第一个 loki
        """
        import time as _time

        rows = self._client(host).loki_logs(
            selector, limit=n,
            since_ns=_parse_since(since),
            filters=tuple(filter) if isinstance(filter, str) else tuple(filter),
            datasource_uid=datasource)
        if not rows:
            self._r.warn("没有匹配的日志")
            return 1
        for ts, who, line in rows:
            stamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts / 1e9))
            print(f"{stamp} {who} {line}")
        return 0

    @cmd
    def api(self, method: str, path: str, data="", host: str = "", **params):
        """直接调用任意 Grafana API。"""
        emit(self._client(host).request(method, path, json_body=parse_data(data), params=params or None))
        return 0


def _parse_since(text: str) -> int:
    import re

    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip().lower())
    if not m:
        raise GrafanaError(f"--since 不认识: {text}（支持 30m / 24h / 7d）")
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return int(m.group(1)) * unit * 10**9


def main():
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        _print_help()
        raise SystemExit(0)
    if len(sys.argv) <= 1:
        sys.argv.append("--skills")
    timed(run_cli, label="grafana")(GrafanaCli())
