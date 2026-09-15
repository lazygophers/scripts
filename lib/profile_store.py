"""按 key 分 profile 的凭据存储：原子写盘 + 跨进程锁 + 0600。

`archery`（key=域名）、`grafana`（key=域名）、`email`（key=邮箱地址）共用这一份。
以前 archery 和 grafana 各存了一份逐行相同的实现，第三个工具进来时抽的。

配置文件长这样（`~/.config/lazygophers/scripts/<name>.yaml`）：

    current: <默认用哪个>
    profiles:
      <key>:
        ...每个工具自己的字段

三条不能省的性质：

- **原子写**：先写同目录临时文件再 `os.replace` 换名。换名是原子的，别的进程要么
  读到旧的完整内容、要么读到新的完整内容，不会读到写了一半的文件。
- **0600**：文件里有明文密码 / 授权码 / TOTP 密钥，先建 0600 再写，避免 umask
  宽松时短暂可读。
- **跨进程锁**：同时跑好几条命令时，两个进程各自「读—改—写」会互相覆盖。锁加在
  同目录的 `.lock` 文件上，配置本身照旧原子换名，读的人不需要拿锁。
"""

from __future__ import annotations

import contextlib
import os
import pathlib


def mask(value: str) -> str:
    """密码 / 密钥打码，两头留 2 位。"""
    if not value:
        return "(未设置)"
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


class ProfileStore:
    """一个工具的配置文件 + 它的 profile 解析规则。

    `key_fn` 把用户输入规范化成 profile 的 key（archery/grafana 取域名，email 取
    小写邮箱）。`error` 是该工具自己的异常类，报错文案里的下一步动作用 `tool` /
    `noun` / `key_flag` 拼，这样三个工具的提示都说自己的命令名。
    """

    def __init__(self, filename: str, *, error: type[Exception], key_fn,
                 tool: str, noun: str = "站点", key_flag: str = "--host",
                 login_flag: str = "--url", key_hint: str = "<域名>", path_resolver=None):
        self.filename = filename
        self.error = error
        self.key_fn = key_fn
        self.tool = tool
        self.noun = noun
        self.key_flag = key_flag      # 临时指定用哪个 profile 的选项
        self.login_flag = login_flag  # login 子命令里指定目标的选项
        self.key_hint = key_hint
        self._path_resolver = path_resolver

    # ------------------------------------------------------------ 路径

    def default_path(self) -> pathlib.Path:
        """配置文件路径。给了 `path_resolver` 就用它（archery 在 sudo 下要回落到
        发起 sudo 的用户家目录），否则就是当前用户的 ~/.config/lazygophers/scripts/。"""
        if self._path_resolver is not None:
            return self._path_resolver()
        return pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / self.filename

    def _target(self, path: pathlib.Path | None) -> pathlib.Path:
        return path or self.default_path()

    # ------------------------------------------------------------ 读写

    def load(self, path: pathlib.Path | None = None) -> dict:
        """读配置；文件不存在返回空 dict。"""
        import yaml

        target = self._target(path)
        if not target.exists():
            return {}
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def save(self, data: dict, path: pathlib.Path | None = None) -> None:
        """写配置，权限 0600，原子换名。"""
        import yaml

        target = self._target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        os.chmod(target, 0o600)

    @contextlib.contextmanager
    def lock(self, path: pathlib.Path | None = None):
        """跨进程互斥锁，锁住配置的「读—改—写」。"""
        import fcntl

        target = self._target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target.with_name(f".{target.name}.lock")), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # ------------------------------------------------------------ profile

    def profiles(self, cfg: dict) -> dict:
        """配置里的所有 profile。"""
        got = cfg.get("profiles")
        return dict(got) if isinstance(got, dict) else {}

    def resolve(self, cfg: dict, key: str = "") -> tuple[str, dict]:
        """挑出要用的 profile：显式指定优先，其次 `current`，只有一个时直接用它。

        返回 (key, profile)。找不到时抛该工具自己的异常，消息里带下一步动作。
        """
        all_p = self.profiles(cfg)
        if not all_p:
            raise self.error(f"还没有配置任何{self.noun}（{self.default_path()} 为空）。跑 `{self.tool} login`")
        if key:
            k = self.key_fn(key)
            if k not in all_p:
                known = ", ".join(sorted(all_p)) or "(无)"
                raise self.error(
                    f"没有这个{self.noun}的配置: {k}（已配置: {known}）。跑 `{self.tool} login {self.login_flag} {key}`"
                )
            return k, dict(all_p[k])
        current = str(cfg.get("current") or "")
        if current and current in all_p:
            return current, dict(all_p[current])
        if len(all_p) == 1:
            only = next(iter(all_p))
            return only, dict(all_p[only])
        known = ", ".join(sorted(all_p))
        raise self.error(
            f"配了多个{self.noun}但没指定用哪个（{known}）。跑 `{self.tool} use {self.key_hint}` 或加 {self.key_flag}"
        )

    def put(self, cfg: dict, key: str, profile: dict) -> dict:
        """把 profile 写回 cfg（不落盘），没有 current 时顺手设成它。"""
        all_p = self.profiles(cfg)
        all_p[key] = profile
        cfg["profiles"] = all_p
        if not cfg.get("current"):
            cfg["current"] = key
        return cfg
